import asyncio
import time

import pytest

pytest.importorskip("slixmpp")
aiosqlite = pytest.importorskip("aiosqlite")

from banbot.cache import CacheMixin
from banbot.db import DatabaseMixin
from banbot.moderation import ModerationMixin
from banbot.utils import bare_jid


class FlowBot(ModerationMixin, DatabaseMixin, CacheMixin):
    def __init__(self):
        self.protected_rooms = {"room@conference.example.test"}
        self.occupants = {
            "room@conference.example.test": {
                "Nick": {"jid": "user@example.org/resource", "affiliation": "member"},
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
        self.sent = []
        self.applied_bans = []
        self.applied_unbans = []
        self.published = []
        self.retracted = []
        self.events = []
        self.audit_events = []
        self.protect_result = (False, None)

    def bare_jid(self, jid):
        return bare_jid(jid)

    def is_ignored_target(self, *args, **kwargs):
        return False

    async def is_protected_admin_target(self, *args, **kwargs):
        return self.protect_result

    async def apply_ban_to_room(self, room, ban_jid, ban_nick, comment, issuer=None, announce_missing_rights=True):
        self.applied_bans.append((room, ban_jid, ban_nick, comment, issuer))

    async def apply_unban_to_room(self, room, ban_jid, ban_nick, domain=None):
        self.applied_unbans.append((room, ban_jid, ban_nick, domain))

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


async def make_bot():
    bot = FlowBot()
    await bot.setup_db()
    return bot


@pytest.mark.asyncio
async def test_ban_all_persists_publishes_and_applies_to_protected_rooms(temp_db_path, monkeypatch):
    import banbot.moderation as moderation_module

    monkeypatch.setattr(moderation_module, "ADMIN_ROOM", "admin@conference.example.test")
    bot = await make_bot()
    try:
        await bot.ban_all("User@Example.org/Device", None, issuer="admin@example.test", comment="spam")

        assert "user@example.org" in bot.ban_index_by_jid
        assert bot.applied_bans == [
            ("room@conference.example.test", "user@example.org", "nick", "spam", "admin@example.test")
        ]
        assert bot.published == [("user@example.org", None, "spam")]
        assert any("✅ Banned user@example.org" in msg["mbody"] for msg in bot.sent)
    finally:
        await bot.db.close()


@pytest.mark.asyncio
async def test_ban_all_refuses_admin_protected_target(temp_db_path, monkeypatch):
    import banbot.moderation as moderation_module

    monkeypatch.setattr(moderation_module, "ADMIN_ROOM", "admin@conference.example.test")
    bot = await make_bot()
    bot.protect_result = (True, "user@example.org is admin/owner")
    try:
        await bot.ban_all("user@example.org", None, issuer="admin@example.test", comment="spam")

        assert bot.applied_bans == []
        assert bot.published == []
        async with bot.db.execute("SELECT COUNT(*) FROM bans") as cursor:
            assert (await cursor.fetchone())[0] == 0
        assert "Refusing ban" in bot.sent[-1]["mbody"]
    finally:
        await bot.db.close()


@pytest.mark.asyncio
async def test_unban_all_removes_db_cache_applies_unban_and_retracts(temp_db_path, monkeypatch):
    import banbot.moderation as moderation_module

    monkeypatch.setattr(moderation_module, "ADMIN_ROOM", "admin@conference.example.test")
    bot = await make_bot()
    try:
        await bot.upsert_ban_db("user@example.org", "nick", 0, "admin@example.test", "spam")
        await bot.load_bans_from_db()

        await bot.unban_all("user@example.org", issuer="admin@example.test")

        assert "user@example.org" not in bot.ban_index_by_jid
        assert bot.applied_unbans == [("room@conference.example.test", "user@example.org", "nick", None)]
        assert bot.retracted == [("user@example.org", None)]
        assert any("♻️ Unbanned user@example.org" in msg["mbody"] for msg in bot.sent)
    finally:
        await bot.db.close()


@pytest.mark.asyncio
async def test_tempban_duration_limit_is_enforced(temp_db_path, monkeypatch):
    import banbot.moderation as moderation_module

    monkeypatch.setattr(moderation_module, "ADMIN_ROOM", "admin@conference.example.test")
    bot = await make_bot()
    bot.max_tempban_days = 1
    try:
        await bot.ban_all("user@example.org", int(time.time()) + 3 * 86400, issuer="admin@example.test")

        assert bot.applied_bans == []
        assert "MAX_TEMPBAN_DAYS" in bot.sent[-1]["mbody"]
    finally:
        await bot.db.close()
