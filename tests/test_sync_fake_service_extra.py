"""sync.py tests using a fake MUC affiliation service and temporary SQLite DB."""

from __future__ import annotations

import time

import pytest

aiosqlite = pytest.importorskip("aiosqlite")

from banbot.cache import CacheMixin
from banbot.db import DatabaseMixin
from banbot.sync import SyncMixin
from banbot.utils import bare_jid, safe_jid as real_safe_jid


class FakeMucService:
    """Fake MUC plugin used by sync tests.

    The test double records room joins/leaves and returns configured
    affiliation lookups for both legacy tuple keys and nested mappings.
    """

    def __init__(self, affiliations=None, fail_join_rooms=None):
        self.affiliations = self._normalize_affiliations(affiliations)
        self.fail_join_rooms = set(fail_join_rooms or [])
        self.calls = []
        self.left = []
        self.joined = []

    @staticmethod
    def _normalize_affiliations(affiliations):
        if not affiliations:
            return {}
        if all(isinstance(key, tuple) and len(key) == 2 for key in affiliations):
            return {key: list(users) for key, users in affiliations.items()}
        normalized = {}
        for room, affiliation_map in affiliations.items():
            for affiliation, users in affiliation_map.items():
                normalized[(room, affiliation)] = list(users)
        return normalized

    def leave_muc(self, room, nick):
        self.left.append((room, nick))

    def join_muc(self, room, nick):
        if room in self.fail_join_rooms:
            raise RuntimeError(f"join failed for {room}")
        self.joined.append((room, nick))

    async def get_users_by_affiliation(self, room, affiliation):
        self.calls.append((room, affiliation))
        return list(self.affiliations.get((room, affiliation), []))


class SyncBot(SyncMixin, DatabaseMixin, CacheMixin):
    def __init__(self, db_path):
        self._test_db_path = str(db_path)
        self.protected_rooms = {"room@conference.example.test"}
        self.occupants = {}
        self.plugin = {"xep_0045": FakeMucService()}
        self.ban_cache = {}
        self.ban_index_by_jid = {}
        self.ban_index_by_nick = {}
        self.ban_index_by_domain = {}
        self.sent = []
        self.room_join_time = {}
        self.bot_admin_state = {}
        self.sync_batch_size = 10
        self.applied = []
        self.unbanned = []
        self.auto_redactions = []
        self.admin_rooms = {"room@conference.example.test"}

    async def setup_db(self, *, create_startup_backup: bool = True) -> None:
        """Initialize the test database using the explicit temp_db_path fixture."""
        import banbot.db as db_module

        original_db_file = db_module.DB_FILE
        db_module.DB_FILE = self._test_db_path
        try:
            await super().setup_db(create_startup_backup=create_startup_backup)
        finally:
            db_module.DB_FILE = original_db_file

    @staticmethod
    def bare_jid(jid):
        return bare_jid(jid)

    @staticmethod
    def safe_jid(jid):
        return real_safe_jid(jid)

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

    async def maybe_auto_redact_after_manual_muc_ban(self, jid, reason, actor=None):
        self.auto_redactions.append((jid, reason, actor))


async def make_bot(temp_db_path):
    bot = SyncBot(temp_db_path)
    await bot.setup_db()
    await bot.load_bans_from_db()
    return bot


@pytest.mark.asyncio
async def test_sync_bans_to_rooms_applies_only_missing_bans(temp_db_path, monkeypatch):
    import banbot.sync as sync_module

    monkeypatch.setattr(sync_module, "ADMIN_ROOM", "admin@conference.example.test")
    bot = await make_bot(temp_db_path)
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
    bot = await make_bot(temp_db_path)
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
async def test_sync_single_room_does_not_auto_redact_known_outcast(temp_db_path):
    bot = await make_bot(temp_db_path)
    bot.plugin["xep_0045"] = FakeMucService(
        {("room@conference.example.test", "outcast"): [("known@example.test", "spam")]}
    )
    try:
        await bot.upsert_ban_db("known@example.test", None, 0, "tester", "spam")

        await bot.sync_bans_to_rooms_for_single_room("room@conference.example.test")

        assert bot.auto_redactions == []
        assert bot.applied == []
    finally:
        await bot.db.close()


@pytest.mark.asyncio
async def test_sync_single_room_auto_redacts_recovered_outcast_when_reason_is_discovered(temp_db_path):
    bot = await make_bot(temp_db_path)
    bot.plugin["xep_0045"] = FakeMucService(
        {("room@conference.example.test", "outcast"): [("known@example.test", "spam")]}
    )
    try:
        await bot.upsert_ban_db("known@example.test", None, 0, "tester", "Recovered from room")

        await bot.sync_bans_to_rooms_for_single_room("room@conference.example.test")

        assert bot.auto_redactions == [("known@example.test", "spam", "sync_room_add")]
    finally:
        await bot.db.close()


@pytest.mark.asyncio
async def test_sync_single_room_auto_redacts_new_orphan_outcast(temp_db_path):
    bot = await make_bot(temp_db_path)
    bot.plugin["xep_0045"] = FakeMucService(
        {("room@conference.example.test", "outcast"): [("orphan@example.test", "spam")]}
    )
    try:
        await bot.sync_bans_to_rooms_for_single_room("room@conference.example.test")

        assert bot.auto_redactions == [("orphan@example.test", "spam", "sync_room_add")]
    finally:
        await bot.db.close()


@pytest.mark.asyncio
async def test_sync_single_room_unbans_expired_tempban_outcast_instead_of_recovering(temp_db_path):
    bot = await make_bot(temp_db_path)
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
    bot = await make_bot(temp_db_path)
    bot.admin_rooms = set()
    try:
        await bot.upsert_ban_db("new@example.test", None, 0, "tester", "new")
        await bot.sync_bans_to_rooms(startup=False, announce_progress=True)

        assert bot.applied == []
        assert any("has no admin/owner rights" in msg["mbody"] for msg in bot.sent)
    finally:
        await bot.db.close()


@pytest.mark.asyncio
async def test_fake_muc_service_accepts_nested_affiliation_mapping():
    service = FakeMucService(
        {
            "room@example.test": {
                "owner": ["owner@example.test"],
                "admin": ["admin@example.test"],
            }
        }
    )

    assert service.affiliations == {
        ("room@example.test", "owner"): ["owner@example.test"],
        ("room@example.test", "admin"): ["admin@example.test"],
    }
    assert await service.get_users_by_affiliation("room@example.test", "owner") == [
        "owner@example.test"
    ]
    assert await service.get_users_by_affiliation("room@example.test", "admin") == [
        "admin@example.test"
    ]
    assert await service.get_users_by_affiliation("room@example.test", "outcast") == []
    assert await service.get_users_by_affiliation("missing-room@example.test", "owner") == []


@pytest.mark.asyncio
async def test_fake_muc_service_accepts_tuple_affiliation_mapping():
    service = FakeMucService(
        {
            ("room@example.test", "owner"): ["owner@example.test"],
            ("room@example.test", "admin"): ["admin@example.test"],
        }
    )

    assert service.affiliations == {
        ("room@example.test", "owner"): ["owner@example.test"],
        ("room@example.test", "admin"): ["admin@example.test"],
    }
    assert await service.get_users_by_affiliation("room@example.test", "owner") == [
        "owner@example.test"
    ]
    assert await service.get_users_by_affiliation("room@example.test", "admin") == [
        "admin@example.test"
    ]


def test_fake_muc_service_handles_empty_affiliation_mapping():
    assert FakeMucService().affiliations == {}
    assert FakeMucService({}).affiliations == {}


@pytest.mark.asyncio
async def test_sync_admins_populates_owner_and_admin_occupants(temp_db_path, monkeypatch):
    import banbot.sync as sync_module

    monkeypatch.setattr(sync_module, "ADMIN_ROOM", "admin@conference.example.test")
    bot = await make_bot(temp_db_path)
    muc_service = FakeMucService(
        {
            ("admin@conference.example.test", "owner"): ["owner@example.test"],
            ("admin@conference.example.test", "admin"): ["admin@example.test"],
        }
    )
    bot.plugin["xep_0045"] = muc_service
    try:
        await bot.sync_admins(announce=True)

        occupants = bot.occupants["admin@conference.example.test"]
        assert occupants["owner@example.test"]["affiliation"] == "owner"
        assert occupants["admin@example.test"]["affiliation"] == "admin"
        assert {
            ("admin@conference.example.test", "owner"),
            ("admin@conference.example.test", "admin"),
        } <= set(muc_service.calls)
        assert "Current admins/owners" in bot.sent[-1]["mbody"]
    finally:
        await bot.db.close()


@pytest.mark.asyncio
async def test_wait_for_bot_admin_rights_returns_immediate_final_state_without_sleep(temp_db_path):
    bot = await make_bot(temp_db_path)
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
    bot = await make_bot(temp_db_path)
    bot.protected_rooms = set()
    try:
        await bot.sync_rooms_and_bans()

        assert bot.sent[-1]["mbody"] == "⚠️ No protected rooms to sync."
    finally:
        await bot.db.close()


@pytest.mark.asyncio
async def test_sync_rooms_and_bans_uses_configured_batch_size(temp_db_path, monkeypatch):
    import banbot.sync as sync_module

    monkeypatch.setattr(sync_module, "ADMIN_ROOM", "admin@conference.example.test")

    async def no_sleep(_delay):
        return None

    monkeypatch.setattr(sync_module.asyncio, "sleep", no_sleep)
    bot = await make_bot(temp_db_path)
    bot.protected_rooms = {
        "room-a@conference.example.test",
        "room-b@conference.example.test",
    }
    bot.admin_rooms = set(bot.protected_rooms)
    bot.occupants = {
        room: {sync_module.NICK: {"jid": "bot@example.test"}}
        for room in bot.protected_rooms
    }
    bot.plugin["xep_0045"] = FakeMucService()
    bot.sync_batch_size = 1
    try:
        await bot.sync_rooms_and_bans()

        batch_messages = [
            msg["mbody"]
            for msg in bot.sent
            if msg["mbody"].startswith("⏳ Syncing batch")
        ]
        assert len(batch_messages) == 2
        assert any("1-1/2" in message for message in batch_messages)
        assert any("2-2/2" in message for message in batch_messages)
    finally:
        await bot.db.close()


def prepare_bot_for_room_sync(bot, sync_module, room):
    """Prepare a SyncBot instance as if it already occupies one protected room."""
    bot.protected_rooms = {room}
    bot.admin_rooms = {room}
    bot.occupants = {room: {sync_module.NICK: {"jid": "bot@example.test"}}}


async def make_bot_for_join_time_check(temp_db_path, sync_module, room, *, fail_join=False):
    """Build a room-sync bot with explicit DB path and optional join failure."""
    bot = await make_bot(temp_db_path)
    prepare_bot_for_room_sync(bot, sync_module, room)
    failed_rooms = {room} if fail_join else None
    bot.plugin["xep_0045"] = FakeMucService(fail_join_rooms=failed_rooms)
    return bot


@pytest.mark.asyncio
async def test_sync_rooms_and_bans_does_not_set_join_time_when_join_fails(temp_db_path, monkeypatch):
    import banbot.sync as sync_module

    monkeypatch.setattr(sync_module, "ADMIN_ROOM", "admin@conference.example.test")

    async def no_sleep(_delay):
        return None

    monkeypatch.setattr(sync_module.asyncio, "sleep", no_sleep)
    room = "room@conference.example.test"
    bot = await make_bot_for_join_time_check(
        temp_db_path, sync_module, room, fail_join=True
    )
    try:
        await bot.sync_rooms_and_bans()

        assert room not in bot.room_join_time
        assert bot.bot_admin_state[room] is True
    finally:
        await bot.db.close()


@pytest.mark.asyncio
async def test_sync_rooms_and_bans_sets_join_time_after_success(temp_db_path, monkeypatch):
    import banbot.sync as sync_module

    monkeypatch.setattr(sync_module, "ADMIN_ROOM", "admin@conference.example.test")

    async def no_sleep(_delay):
        return None

    monkeypatch.setattr(sync_module.asyncio, "sleep", no_sleep)
    room = "room-ok@conference.example.test"
    bot = await make_bot_for_join_time_check(temp_db_path, sync_module, room)
    try:
        await bot.sync_rooms_and_bans()

        assert room in bot.room_join_time
    finally:
        await bot.db.close()
