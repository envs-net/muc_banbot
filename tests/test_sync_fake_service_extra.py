"""sync.py tests using a fake MUC affiliation service and temporary SQLite DB."""

from __future__ import annotations

import time

import pytest

aiosqlite = pytest.importorskip("aiosqlite")

from banbot.cache import CacheMixin
from banbot.db import DatabaseMixin
from banbot.sync import SyncMixin
from banbot.utils import bare_jid, safe_jid


class FakeMucService:
    def __init__(self, affiliations=None):
        self.affiliations = affiliations or {}
        self.calls = []

    async def get_users_by_affiliation(self, room, affiliation):
        self.calls.append((room, affiliation))
        return list(self.affiliations.get((room, affiliation), []))


class SyncBot(SyncMixin, DatabaseMixin, CacheMixin):
    def __init__(self):
        self.protected_rooms = {"room@conference.example.test"}
        self.occupants = {}
        self.plugin = {"xep_0045": FakeMucService()}
        self.ban_cache = {}
        self.ban_index_by_jid = {}
        self.ban_index_by_nick = {}
        self.ban_index_by_domain = {}
        self.sent = []
        self.applied = []
        self.unbanned = []
        self.admin_rooms = {"room@conference.example.test"}

    @staticmethod
    def bare_jid(jid):
        return bare_jid(jid)

    @staticmethod
    def safe_jid(jid):
        return safe_jid(jid)

    def is_bot_admin_or_owner(self, room):
        return room in self.admin_rooms

    async def bot_send_message(self, **kwargs):
        self.sent.append(kwargs)

    async def apply_ban_to_room(self, room, ban_jid, ban_nick, comment, announce_missing_rights=True):
        self.applied.append((room, ban_jid, ban_nick, comment, announce_missing_rights))

    async def unban_all(self, target, issuer="system"):
        self.unbanned.append((target, issuer))
        await self.delete_ban_db(target)
        await self.load_bans_from_db()


async def make_bot():
    bot = SyncBot()
    await bot.setup_db()
    await bot.load_bans_from_db()
    return bot


@pytest.mark.asyncio
async def test_sync_bans_to_rooms_applies_only_missing_bans(temp_db_path, monkeypatch):
    import banbot.sync as sync_module

    monkeypatch.setattr(sync_module, "ADMIN_ROOM", "admin@conference.example.test")
    bot = await make_bot()
    bot.plugin["xep_0045"] = FakeMucService(
        {("room@conference.example.test", "outcast"): ["already@example.test"]}
    )
    try:
        await bot.upsert_ban_db("already@example.test", None, 0, "tester", "old")
        await bot.upsert_ban_db("new@example.test", None, 0, "tester", "new")

        await bot.sync_bans_to_rooms(startup=True, announce_progress=True)

        assert bot.applied == [
            ("room@conference.example.test", "new@example.test", None, "new", False)
        ]
        assert any("Finished syncing room" in msg["mbody"] for msg in bot.sent)
    finally:
        await bot.db.close()


@pytest.mark.asyncio
async def test_sync_single_room_recovers_orphan_outcast(temp_db_path):
    bot = await make_bot()
    bot.plugin["xep_0045"] = FakeMucService(
        {("room@conference.example.test", "outcast"): ["orphan@example.test"]}
    )
    try:
        await bot.sync_bans_to_rooms_for_single_room("room@conference.example.test")
        await bot.load_bans_from_db()

        assert "orphan@example.test" in bot.ban_index_by_jid
        assert bot.applied == []  # recovered outcast was already present in the room
    finally:
        await bot.db.close()


@pytest.mark.asyncio
async def test_sync_single_room_unbans_expired_tempban_outcast_instead_of_recovering(temp_db_path):
    bot = await make_bot()
    bot.plugin["xep_0045"] = FakeMucService(
        {("room@conference.example.test", "outcast"): ["expired@example.test"]}
    )
    try:
        await bot.upsert_ban_db("expired@example.test", None, int(time.time()) - 5, "tester", "expired")
        await bot.sync_bans_to_rooms_for_single_room("room@conference.example.test")

        assert bot.unbanned == [("expired@example.test", "system")]
        await bot.load_bans_from_db()
        assert "expired@example.test" not in bot.ban_index_by_jid
    finally:
        await bot.db.close()


@pytest.mark.asyncio
async def test_sync_bans_to_rooms_skips_room_without_bot_admin_rights(temp_db_path, monkeypatch):
    import banbot.sync as sync_module

    monkeypatch.setattr(sync_module, "ADMIN_ROOM", "admin@conference.example.test")
    bot = await make_bot()
    bot.admin_rooms = set()
    try:
        await bot.upsert_ban_db("new@example.test", None, 0, "tester", "new")
        await bot.sync_bans_to_rooms(startup=False, announce_progress=True)

        assert bot.applied == []
        assert any("has no admin/owner rights" in msg["mbody"] for msg in bot.sent)
    finally:
        await bot.db.close()


@pytest.mark.asyncio
async def test_sync_admins_populates_owner_and_admin_occupants(temp_db_path, monkeypatch):
    import banbot.sync as sync_module

    monkeypatch.setattr(sync_module, "ADMIN_ROOM", "admin@conference.example.test")
    bot = await make_bot()
    bot.plugin["xep_0045"] = FakeMucService(
        {
            ("admin@conference.example.test", "owner"): ["owner@example.test"],
            ("admin@conference.example.test", "admin"): ["admin@example.test"],
        }
    )
    try:
        await bot.sync_admins(announce=True)

        occupants = bot.occupants["admin@conference.example.test"]
        assert occupants["owner@example.test"]["affiliation"] == "owner"
        assert occupants["admin@example.test"]["affiliation"] == "admin"
        assert "Current admins/owners" in bot.sent[-1]["mbody"]
    finally:
        await bot.db.close()


@pytest.mark.asyncio
async def test_wait_for_bot_admin_rights_returns_immediate_final_state_without_sleep(temp_db_path):
    bot = await make_bot()
    try:
        bot.admin_rooms = {"room@conference.example.test"}
        assert await bot._wait_for_bot_admin_rights("room@conference.example.test", timeout=0, interval=0) is True

        bot.admin_rooms = set()
        assert await bot._wait_for_bot_admin_rights("room@conference.example.test", timeout=0, interval=0) is False
    finally:
        await bot.db.close()


@pytest.mark.asyncio
async def test_sync_rooms_and_bans_reports_empty_room_set(temp_db_path, monkeypatch):
    import banbot.sync as sync_module

    monkeypatch.setattr(sync_module, "ADMIN_ROOM", "admin@conference.example.test")
    bot = await make_bot()
    bot.protected_rooms = set()
    try:
        await bot.sync_rooms_and_bans()

        assert bot.sent[-1]["mbody"] == "⚠️ No protected rooms to sync."
    finally:
        await bot.db.close()
