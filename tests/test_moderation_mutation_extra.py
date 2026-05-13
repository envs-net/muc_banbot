"""Focused moderation tests for mutation-sensitive safety branches."""

from __future__ import annotations

import asyncio
import time

import pytest

pytest.importorskip("aiosqlite")
pytest.importorskip("slixmpp")

from banbot.cache import CacheMixin
from banbot.db import DatabaseMixin
from banbot.moderation import ModerationMixin
from banbot.utils import bare_jid


class FakeMuc:
    def __init__(self):
        self.affiliations = []
        self.roles = []

    async def set_affiliation(self, **kwargs):
        self.affiliations.append(kwargs)

    async def set_role(self, **kwargs):
        self.roles.append(kwargs)


class MutationModerationBot(ModerationMixin, DatabaseMixin, CacheMixin):
    def __init__(self):
        self.protected_rooms = {"room@conference.example.test"}
        self.occupants = {
            "room@conference.example.test": {
                "BanBot": {"jid": "bot@example.test/res", "affiliation": "admin", "role": "moderator"},
                "Nick": {"jid": "user@example.test/res", "affiliation": "member", "role": "participant"},
                "Admin": {"jid": "admin@example.test/res", "affiliation": "admin", "role": "moderator"},
                "Domain": {"jid": "bad@spam.example/res", "affiliation": "member", "role": "participant"},
            }
        }
        self.ban_cache = {}
        self.ban_index_by_jid = {}
        self.ban_index_by_nick = {}
        self.ban_index_by_domain = {}
        self.max_tempban_days = 30
        self.allow_user_cmds = True
        self.show_ban_in_muc = True
        self.muc_write_semaphore = asyncio.Semaphore(10)
        self.plugin = {"xep_0045": FakeMuc()}
        self.sent = []
        self.published = []
        self.retracted = []
        self.events = []
        self.audit_events = []
        self.protect_result = (False, None)

    def bare_jid(self, jid):
        return bare_jid(jid)

    def is_bot_admin_or_owner(self, room):
        info = self.occupants.get(room, {}).get("BanBot")
        return bool(info and info.get("affiliation") in ("admin", "owner"))

    def is_ignored_target(self, *args, **kwargs):
        return False

    async def is_protected_admin_target(self, *args, **kwargs):
        return self.protect_result

    async def rtbl_publish_ban(self, jid=None, domain=None, comment=None):
        self.published.append((jid, domain, comment))

    async def rtbl_retract_ban(self, jid=None, domain=None):
        self.retracted.append((jid, domain))

    def log_event(self, level, event, **fields):
        self.events.append((level, event, fields))

    async def audit_event(self, event_type, **kwargs):
        self.audit_events.append((event_type, kwargs))

    async def bot_send_message(self, **kwargs):
        self.sent.append(kwargs)

    async def notify_protected(self, room, message):
        await self.bot_send_message(mto=room, mbody=message, mtype="groupchat")


async def make_bot():
    bot = MutationModerationBot()
    await bot.setup_db()
    await bot.load_bans_from_db()
    return bot


@pytest.mark.asyncio
async def test_ban_all_rejects_plain_domain_and_invalid_jid(temp_db_path, monkeypatch):
    import banbot.moderation as moderation_module

    monkeypatch.setattr(moderation_module, "ADMIN_ROOM", "admin@conference.example.test")
    bot = await make_bot()
    try:
        await bot.ban_all("example.org", None, issuer="admin@example.test")
        assert "Invalid domain ban" in bot.sent[-1]["mbody"]

        await bot.ban_all("user@example", None, issuer="admin@example.test")
        assert "Invalid JID format" in bot.sent[-1]["mbody"]
        assert bot.published == []
    finally:
        await bot.db.close()


@pytest.mark.asyncio
async def test_ban_all_updates_existing_active_jid_ban_for_matching_nick(temp_db_path, monkeypatch):
    import banbot.moderation as moderation_module

    monkeypatch.setattr(moderation_module, "ADMIN_ROOM", "admin@conference.example.test")
    bot = await make_bot()
    try:
        await bot.upsert_ban_db("user@example.test", "nick", 0, "old", "old comment")
        await bot.load_bans_from_db()

        until = int(time.time()) + 60
        await bot.ban_all("nick", until, issuer="admin@example.test", comment="updated")

        async with bot.db.execute("SELECT jid, nick, until, issuer, comment FROM bans") as cur:
            rows = await cur.fetchall()
        assert len(rows) == 1
        assert rows[0][0] == "user@example.test"
        assert rows[0][1] == "nick"
        assert rows[0][2] == until
        assert rows[0][3] == "admin@example.test"
        assert rows[0][4] == "updated"
        assert bot.retracted == [("user@example.test", None)]
        assert any(event[0] == "ban_updated" for event in bot.audit_events)
    finally:
        await bot.db.close()


@pytest.mark.asyncio
async def test_domain_ban_publishes_domain_and_applies_only_matching_occupants(temp_db_path, monkeypatch):
    import banbot.moderation as moderation_module

    monkeypatch.setattr(moderation_module, "ADMIN_ROOM", "admin@conference.example.test")
    bot = await make_bot()
    try:
        await bot.ban_all("*.spam.example", None, issuer="admin@example.test", comment="domain spam")

        assert bot.published == [(None, "spam.example", "domain spam")]
        assert [call["jid"] for call in bot.plugin["xep_0045"].affiliations] == ["bad@spam.example"]
        assert {call["nick"] for call in bot.plugin["xep_0045"].roles} == {"Domain"}
        assert "*.spam.example" in bot.ban_cache
    finally:
        await bot.db.close()


@pytest.mark.asyncio
async def test_unban_all_rejects_plain_domain_and_missing_target(temp_db_path, monkeypatch):
    import banbot.moderation as moderation_module

    monkeypatch.setattr(moderation_module, "ADMIN_ROOM", "admin@conference.example.test")
    bot = await make_bot()
    try:
        await bot.unban_all("example.org", issuer="admin@example.test")
        assert "Invalid domain unban" in bot.sent[-1]["mbody"]

        await bot.unban_all("missing@example.test", issuer="admin@example.test")
        assert "No ban found" in bot.sent[-1]["mbody"]
    finally:
        await bot.db.close()


@pytest.mark.asyncio
async def test_unban_all_can_resolve_jid_ban_by_localpart(temp_db_path, monkeypatch):
    import banbot.moderation as moderation_module

    monkeypatch.setattr(moderation_module, "ADMIN_ROOM", "admin@conference.example.test")
    bot = await make_bot()
    try:
        await bot.upsert_ban_db("user@example.test", "nick", 0, "admin@example.test", "spam")
        await bot.load_bans_from_db()

        await bot.unban_all("user", issuer="admin@example.test")

        assert "user@example.test" not in bot.ban_index_by_jid
        assert bot.retracted == [("user@example.test", None)]
        assert any("Unbanned user" in msg["mbody"] for msg in bot.sent)
    finally:
        await bot.db.close()
