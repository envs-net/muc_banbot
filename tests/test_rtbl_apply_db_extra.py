"""Additional RTBL application tests covering DB-backed apply/cleanup paths."""

from __future__ import annotations

import pytest

aiosqlite = pytest.importorskip("aiosqlite")

from banbot.cache import CacheMixin
from banbot.db import DatabaseMixin
from banbot.rtbl_apply import RtblApplyMixin
from banbot.rtbl_utils import _rtbl_hash_jid
from banbot.utils import bare_jid, domain_matches


class RtblDbBot(RtblApplyMixin, DatabaseMixin, CacheMixin):
    def __init__(self):
        self.rtbl_enabled = True
        self.rtbl_announce = True
        self.rtbl_hash_cache = {}
        self.rtbl_domain_cache = {}
        self.ignore_jids = set()
        self.ignore_domains = set()
        self.protected_rooms = {"room@conference.example.test"}
        self.occupants = {
            "room@conference.example.test": {
                "Bad": {"jid": "bad@example.test/res"},
                "Spam": {"jid": "spam@bad.example/res"},
                "Admin": {"jid": "admin@bad.example/res"},
            }
        }
        self.ban_cache = {}
        self.ban_index_by_jid = {}
        self.ban_index_by_nick = {}
        self.ban_index_by_domain = {}
        self.sent = []
        self.applied = []
        self.unbanned = []
        self.events = []
        self.audit_events = []
        self.protected_jids = set()

    def bare_jid(self, jid):
        return bare_jid(jid)

    def _rtbl_hash_jid(self, jid):
        return _rtbl_hash_jid(jid)

    def is_ignored_jid(self, jid):
        return bare_jid(jid) in self.ignore_jids

    def is_ignored_domain(self, domain):
        domain = domain.lstrip("*. ").lower().strip()
        return domain in self.ignore_domains or any(domain_matches(domain, ignored) for ignored in self.ignore_domains)

    async def is_protected_admin_target(self, target, nick=None, jid=None):
        bare = bare_jid(jid or target) if (jid or "@" in target) else target
        if bare in self.protected_jids:
            return True, f"{bare} is admin/owner"
        return False, None

    async def apply_ban_to_room(self, room, ban_jid, ban_nick, comment, issuer=None):
        self.applied.append((room, ban_jid, ban_nick, comment, issuer))

    async def unban_all(self, target, issuer="rtbl_cleanup"):
        self.unbanned.append((target, issuer))
        await self.delete_ban_db(target)
        await self.load_bans_from_db()

    async def bot_send_message(self, **kwargs):
        self.sent.append(kwargs)

    def log_event(self, level, event, **fields):
        self.events.append((level, event, fields))

    async def audit_event(self, event_type, **kwargs):
        self.audit_events.append((event_type, kwargs))


async def make_bot():
    bot = RtblDbBot()
    await bot.setup_db()
    await bot.load_bans_from_db()
    return bot


@pytest.mark.asyncio
async def test_rtbl_apply_jid_persists_audits_announces_and_applies(temp_db_path, monkeypatch):
    import config

    monkeypatch.setattr(config, "ADMIN_ROOM", "admin@conference.example.test", raising=False)
    bot = await make_bot()
    try:
        await bot._rtbl_apply_ban_jid("bad@example.test", "Bad", "listed spam")

        assert "bad@example.test" in bot.ban_index_by_jid
        assert bot.applied == [
            (
                "room@conference.example.test",
                "bad@example.test",
                "Bad",
                "RTBL: listed spam",
                "rtbl",
            )
        ]
        assert bot.audit_events[-1][0] == "rtbl_ban_applied"
        assert "RTBL: Banning bad@example.test" in bot.sent[-1]["mbody"]
    finally:
        await bot.db.close()


@pytest.mark.asyncio
async def test_rtbl_apply_jid_respects_ignorelist_and_admin_protection(temp_db_path, monkeypatch):
    import config

    monkeypatch.setattr(config, "ADMIN_ROOM", "admin@conference.example.test", raising=False)
    bot = await make_bot()
    try:
        bot.ignore_jids = {"bad@example.test"}
        await bot._rtbl_apply_ban_jid("bad@example.test", "Bad", "listed spam")
        assert bot.applied == []
        assert "exact ignorelist match" in bot.sent[-1]["mbody"]

        bot.ignore_jids = set()
        bot.protected_jids = {"bad@example.test"}
        await bot._rtbl_apply_ban_jid("bad@example.test", "Bad", "listed spam")
        assert bot.applied == []
        assert "protected admin/owner" in bot.sent[-1]["mbody"]
    finally:
        await bot.db.close()


@pytest.mark.asyncio
async def test_rtbl_apply_domain_persists_concrete_jid_bans_and_skips_protected(temp_db_path, monkeypatch):
    import config

    monkeypatch.setattr(config, "ADMIN_ROOM", "admin@conference.example.test", raising=False)
    bot = await make_bot()
    bot.protected_jids = {"admin@bad.example"}
    try:
        await bot._rtbl_apply_ban_domain("bad.example", "wave")

        assert "spam@bad.example" in bot.ban_index_by_jid
        assert "admin@bad.example" not in bot.ban_index_by_jid
        assert any(call[1] == "spam@bad.example" for call in bot.applied)
        assert "Domain ban *.bad.example" in bot.sent[-1]["mbody"]
    finally:
        await bot.db.close()


@pytest.mark.asyncio
async def test_rtbl_cleanup_removes_only_uncovered_rtbl_bans(temp_db_path):
    bot = await make_bot()
    try:
        bot.rtbl_hash_cache[_rtbl_hash_jid("covered@example.test")] = "still listed"
        bot.rtbl_domain_cache["covered.example"] = "domain listed"
        await bot.upsert_ban_db("covered@example.test", "Covered", 0, "rtbl", "still")
        await bot.upsert_ban_db("user@covered.example", "Domain", 0, "rtbl", "domain")
        await bot.upsert_ban_db("gone@example.test", "Gone", 0, "rtbl", "gone")
        await bot.upsert_ban_db("*.legacy.example", None, 0, "rtbl", "legacy")
        await bot.load_bans_from_db()

        removed = await bot._rtbl_cleanup_stale_persisted_bans("rtbl_test")

        assert removed == 2
        assert ("gone@example.test", "rtbl_test") in bot.unbanned
        assert ("*.legacy.example", "rtbl_test") in bot.unbanned
        assert "covered@example.test" in bot.ban_index_by_jid
        assert "user@covered.example" in bot.ban_index_by_jid
    finally:
        await bot.db.close()
