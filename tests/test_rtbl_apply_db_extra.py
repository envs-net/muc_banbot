"""Additional RTBL application tests covering DB-backed apply/cleanup paths."""

from __future__ import annotations

import logging

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


@pytest.mark.asyncio
async def test_rtbl_apply_jid_locked_without_reason_or_announcement_persists_plain_comment(temp_db_path, monkeypatch):
    import config

    monkeypatch.setattr(config, "ADMIN_ROOM", "admin@conference.example.test", raising=False)
    bot = await make_bot()
    bot.rtbl_announce = False
    try:
        await bot._rtbl_apply_ban_jid_locked("bad@example.test", None, None)

        assert "bad@example.test" in bot.ban_index_by_jid
        assert bot.sent == []
        assert bot.audit_events[-1][1]["comment"] == "RTBL ban"
        assert bot.audit_events[-1][1]["target"] == "bad@example.test"
        assert bot.applied == [
            ("room@conference.example.test", "bad@example.test", None, "RTBL ban", "rtbl")
        ]
    finally:
        await bot.db.close()


@pytest.mark.asyncio
async def test_rtbl_apply_jid_locked_apply_failure_is_logged_but_db_and_audit_remain(temp_db_path, monkeypatch, caplog):
    import config

    monkeypatch.setattr(config, "ADMIN_ROOM", "admin@conference.example.test", raising=False)
    bot = await make_bot()

    async def failing_apply(*_args, **_kwargs):
        raise RuntimeError("apply failed")

    bot.apply_ban_to_room = failing_apply
    try:
        with caplog.at_level(logging.WARNING, logger="banbot.rtbl_apply"):
            await bot._rtbl_apply_ban_jid_locked("bad@example.test", "Bad", "listed spam")

        assert "bad@example.test" in bot.ban_index_by_jid
        assert bot.audit_events[-1][0] == "rtbl_ban_applied"
        assert "Failed to apply JID ban" in caplog.text
    finally:
        await bot.db.close()


@pytest.mark.asyncio
async def test_rtbl_apply_domain_locked_ignorelist_without_announcement_does_not_mutate_db(temp_db_path):
    bot = await make_bot()
    bot.rtbl_announce = False
    bot.ignore_domains = {"bad.example"}
    try:
        await bot._rtbl_apply_ban_domain_locked("*.bad.example", "wave")

        assert bot.sent == []
        assert bot.applied == []
        assert bot.ban_cache == {}
    finally:
        await bot.db.close()


@pytest.mark.asyncio
async def test_rtbl_apply_domain_locked_only_protected_matches_announces_preview_and_skips_db(temp_db_path, monkeypatch):
    import config

    monkeypatch.setattr(config, "ADMIN_ROOM", "admin@conference.example.test", raising=False)
    bot = await make_bot()
    bot.protected_jids = {"spam@bad.example", "admin@bad.example"}
    try:
        await bot._rtbl_apply_ban_domain_locked("bad.example", "wave")

        assert bot.applied == []
        assert bot.ban_cache == {}
        assert "only protected admin/owner matches found" in bot.sent[-1]["mbody"]
        assert "Spam (spam@bad.example)" in bot.sent[-1]["mbody"]
    finally:
        await bot.db.close()


@pytest.mark.asyncio
async def test_rtbl_apply_domain_locked_multiple_matches_audits_each_and_removes_legacy_domain(temp_db_path, monkeypatch):
    import config

    monkeypatch.setattr(config, "ADMIN_ROOM", "admin@conference.example.test", raising=False)
    bot = await make_bot()
    bot.protected_jids = {"admin@bad.example"}
    bot.occupants["room@conference.example.test"]["Second"] = {"jid": "second@bad.example/res"}
    try:
        await bot.upsert_ban_db("*.bad.example", None, 0, "rtbl", "legacy")
        await bot.load_bans_from_db()
        assert "bad.example" in bot.ban_index_by_domain

        await bot._rtbl_apply_ban_domain_locked("*.bad.example", "wave")

        assert "bad.example" not in bot.ban_index_by_domain
        assert "spam@bad.example" in bot.ban_index_by_jid
        assert "second@bad.example" in bot.ban_index_by_jid
        assert "Also matched: 1 more occupant" in bot.sent[-1]["mbody"]
        assert [event for event, _payload in bot.audit_events].count("rtbl_ban_applied") == 2
        assert any(call[1] == "second@bad.example" for call in bot.applied)
    finally:
        await bot.db.close()


@pytest.mark.asyncio
async def test_rtbl_ban_is_still_covered_edge_cases(temp_db_path):
    bot = await make_bot()
    try:
        assert await bot._rtbl_ban_is_still_covered(None) is False
        assert await bot._rtbl_ban_is_still_covered("not-a-jid") is False

        # Wildcard/domain ban rows are not concrete JID bans and must never be
        # considered covered by the RTBL JID/domain cache cleanup check.
        bot.rtbl_domain_cache["legacy.example"] = "domain"
        assert await bot._rtbl_ban_is_still_covered("*.legacy.example") is False
        bot.rtbl_domain_cache.clear()

        bot.rtbl_hash_cache[_rtbl_hash_jid("covered@example.test")] = "hash"
        assert await bot._rtbl_ban_is_still_covered("covered@example.test/res") is True

        bot.rtbl_hash_cache.clear()
        bot.rtbl_domain_cache["covered.example"] = "domain"
        assert await bot._rtbl_ban_is_still_covered("user@covered.example/res") is True
        assert await bot._rtbl_ban_is_still_covered("user@other.example/res") is False
    finally:
        await bot.db.close()


@pytest.mark.asyncio
async def test_rtbl_cleanup_locked_ignores_empty_rows_and_logs_when_removed(temp_db_path, caplog):
    bot = await make_bot()
    try:
        await bot.db.execute(
            "INSERT INTO bans (target_type, target, jid, nick, until, issuer, comment) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("nick", "empty", None, "empty", 0, "rtbl", "empty jid"),
        )
        await bot.upsert_ban_db("gone@example.test", "Gone", 0, "rtbl", "gone")
        await bot.db.commit()
        await bot.load_bans_from_db()

        with caplog.at_level(logging.INFO, logger="banbot.rtbl_apply"):
            removed = await bot._rtbl_cleanup_stale_persisted_bans_locked("cleanup-test")

        assert removed == 1
        assert ("gone@example.test", "cleanup-test") in bot.unbanned
        assert "Removed 1 stale persisted RTBL ban" in caplog.text
    finally:
        await bot.db.close()


@pytest.mark.asyncio
async def test_rtbl_cleanup_locked_uses_default_issuer(temp_db_path):
    bot = await make_bot()
    try:
        await bot.upsert_ban_db("gone@example.test", "Gone", 0, "rtbl", "gone")
        await bot.load_bans_from_db()

        removed = await bot._rtbl_cleanup_stale_persisted_bans_locked()

        assert removed == 1
        assert ("gone@example.test", "rtbl_cleanup") in bot.unbanned
    finally:
        await bot.db.close()
