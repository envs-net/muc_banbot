"""sync.py tests using a fake MUC affiliation service and temporary SQLite DB."""

from __future__ import annotations

import time
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

import pytest

aiosqlite = pytest.importorskip("aiosqlite")

from banbot.cache import CacheMixin
from banbot.db import DatabaseMixin
from banbot.sync import SyncMixin
from banbot.utils import bare_jid, safe_jid as real_safe_jid

# Users listed under an affiliation; each item is either a bare JID string
# or a ``(jid, reason)`` tuple used by some fixture variants.
AffiliationUsers = Sequence[str | tuple[str, str]]
# Legacy flat fixture shape keyed by ``(room_jid, affiliation)``.
FlatAffiliationMap = Mapping[tuple[str, str], AffiliationUsers]
# Preferred nested fixture shape: ``room_jid -> affiliation -> users``.
NestedAffiliationMap = Mapping[str, Mapping[str, AffiliationUsers]]
# Keep expiry short so time-based sync tests complete quickly.
EXPIRY_THRESHOLD_SECONDS = 5
TEST_ADMIN_ROOM = "admin@conference.example.test"
TEST_ROOM = "room@example.test"
TEST_OWNER_JID = "owner@example.test"
TEST_ADMIN_JID = "admin@example.test"
TEST_NEW_OWNER_JID = "new-owner@example.test"
TEST_ROOM_OWNER_ADMIN_NESTED = {
    TEST_ROOM: {
        "owner": [TEST_OWNER_JID],
        "admin": [TEST_ADMIN_JID],
    }
}
TEST_ROOM_OWNER_ADMIN_FLAT = {
    (TEST_ROOM, "owner"): [TEST_OWNER_JID],
    (TEST_ROOM, "admin"): [TEST_ADMIN_JID],
}
TEST_ADMIN_ROOM_OWNER_ADMIN_NESTED = {
    TEST_ADMIN_ROOM: {
        "owner": [TEST_OWNER_JID],
        "admin": [TEST_ADMIN_JID],
    }
}
TEST_ADMIN_ROOM_OWNER_ADMIN_FLAT = {
    (TEST_ADMIN_ROOM, "owner"): [TEST_OWNER_JID],
    (TEST_ADMIN_ROOM, "admin"): [TEST_ADMIN_JID],
}


async def noop_sleep(_delay):
    pass


class FakeMucService:
    """Fake MUC plugin used by sync tests.

    The test double records room joins/leaves and returns configured
    affiliation lookups for both legacy tuple keys and nested mappings.
    """

    def __init__(
        self,
        affiliations: FlatAffiliationMap | NestedAffiliationMap | None = None,
        fail_join_rooms: Sequence[str] | None = None,
    ):
        self.affiliations = self._normalize_affiliations(affiliations)
        self.fail_join_rooms = set(fail_join_rooms or [])
        self.calls = []
        self.left = []
        self.joined = []

    @staticmethod
    def _normalize_affiliations(
        affiliations: FlatAffiliationMap | NestedAffiliationMap | None,
    ) -> dict[tuple[str, str], list]:
        """Return affiliations as a flat (room, affiliation) mapping."""
        if not affiliations:
            return {}
        first_key = next(iter(affiliations))
        if isinstance(first_key, tuple):
            return FakeMucService._copy_flat_affiliations(affiliations)
        return FakeMucService._flatten_nested_affiliations(affiliations)

    @staticmethod
    def _copy_flat_affiliations(
        affiliations: FlatAffiliationMap,
    ) -> dict[tuple[str, str], list]:
        """Copy legacy flat affiliation fixtures into mutable test lists."""
        return {key: list(users) for key, users in affiliations.items()}

    @staticmethod
    def _flatten_nested_affiliations(
        affiliations: NestedAffiliationMap,
    ) -> dict[tuple[str, str], list]:
        """Flatten preferred room -> affiliation -> users fixtures."""
        normalized = {}
        for room, affiliation_map in affiliations.items():
            for affiliation, users in affiliation_map.items():
                normalized[(room, affiliation)] = list(users)
        return normalized

    def leave_muc(self, room, nick):
        """Record room leave calls for leave-path test assertions."""
        self.left.append((room, nick))

    def join_muc(self, room, nick):
        if room in self.fail_join_rooms:
            raise RuntimeError(f"join failed for {room}")
        self.joined.append((room, nick))

    async def get_users_by_affiliation(self, room, affiliation):
        self.calls.append((room, affiliation))
        return list(self.affiliations.get((room, affiliation), []))


@dataclass
class SyncTrackingState:
    """Mutable call tracking state shared by sync test assertions."""

    sent: list = field(default_factory=list)
    applied: list = field(default_factory=list)
    unbanned: list = field(default_factory=list)
    auto_redactions: list = field(default_factory=list)


class SyncBot(SyncMixin, DatabaseMixin, CacheMixin):
    def __init__(self, db_path: str | None) -> None:
        """Initialize a test bot with fake services and isolated state.

        Args:
            db_path: Non-None path to the temporary SQLite database file
                provided by the test fixture. Stored on the instance and used
                by ``setup_db`` when opening the isolated test database.

        The constructor prepares all in-memory fixtures used by sync tests,
        including fake MUC plugin wiring, ban cache/index dictionaries,
        mutable tracking state for assertions, and default room/admin config.
        """
        if db_path is None:
            raise ValueError("db_path must not be None")

        self._test_db_path = str(db_path)
        self.protected_rooms = {"room@conference.example.test"}
        self.occupants = {}
        self.plugin = {"xep_0045": FakeMucService()}
        self.ban_cache = {}
        self.ban_index_by_jid = {}
        self.ban_index_by_nick = {}
        self.ban_index_by_domain = {}
        self.test_state = SyncTrackingState()
        self.room_join_time = {}
        self.bot_admin_state = {}
        self.sync_batch_size = 10
        self.admin_rooms = {"room@conference.example.test"}

    async def setup_db(self, *, create_startup_backup: bool = True) -> None:
        """Initialize an isolated SQLite schema for sync tests.

        Args:
            create_startup_backup: Whether to run optional startup backup
                creation via ``create_startup_database_snapshot`` before
                opening the test database. Keep this ``True`` for normal
                parity with production-like setup. Set to ``False`` in tests
                that intentionally skip backup hooks to reduce side effects or
                avoid requiring backup-related behavior.

        The real ``DatabaseMixin.setup_db`` reads ``banbot.db.DB_FILE`` at
        module level. Opening the explicit temporary test path directly keeps
        this fixture independent from global DB configuration and safe for
        parallel test execution.
        """
        if create_startup_backup and hasattr(self, "create_startup_database_snapshot"):
            await self.create_startup_database_snapshot()

        self.db = await aiosqlite.connect(self._test_db_path)
        await self.db.execute("PRAGMA foreign_keys = ON")
        await self.db.execute(
            """
            CREATE TABLE IF NOT EXISTS bans (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                target_type TEXT NOT NULL CHECK(target_type IN ('jid', 'nick', 'domain')),
                target TEXT NOT NULL,
                jid TEXT,
                nick TEXT,
                until INTEGER NOT NULL DEFAULT 0,
                issuer TEXT,
                comment TEXT,
                created_at INTEGER NOT NULL DEFAULT (strftime('%s','now')),
                updated_at INTEGER NOT NULL DEFAULT (strftime('%s','now')),
                UNIQUE(target_type, target)
            )
            """
        )
        await self.db.execute("CREATE TABLE IF NOT EXISTS rooms (room TEXT PRIMARY KEY)")
        await self.db.execute(
            """
            CREATE TABLE IF NOT EXISTS audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at INTEGER NOT NULL DEFAULT (strftime('%s','now')),
                event_type TEXT NOT NULL,
                actor TEXT,
                room TEXT,
                target_type TEXT,
                target TEXT,
                jid TEXT,
                nick TEXT,
                until INTEGER,
                comment TEXT,
                details TEXT
            )
            """
        )
        await self.db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_bans_target ON bans(target_type, target)")
        await self.db.execute("CREATE INDEX IF NOT EXISTS idx_bans_jid ON bans(jid)")
        await self.db.execute("CREATE INDEX IF NOT EXISTS idx_bans_nick ON bans(LOWER(nick))")
        await self.db.execute("CREATE INDEX IF NOT EXISTS idx_bans_until ON bans(until)")
        await self.db.commit()

        if hasattr(self, "flush_pending_database_backup_audit_events"):
            await self.flush_pending_database_backup_audit_events()

    @staticmethod
    def bare_jid(jid):
        return bare_jid(jid)

    @staticmethod
    def safe_jid(jid):
        return real_safe_jid(jid)

    @property
    def sent(self):
        return self.test_state.sent

    @property
    def applied(self):
        return self.test_state.applied

    @property
    def unbanned(self):
        return self.test_state.unbanned

    @property
    def auto_redactions(self):
        return self.test_state.auto_redactions

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


def test_syncbot_init_rejects_none_db_path() -> None:
    with pytest.raises(ValueError, match="db_path must not be None"):
        SyncBot(None)


async def make_bot(temp_db_path: str) -> SyncBot:
    """Asynchronously create and initialize a SyncBot backed by a temporary test DB.

    This helper must be awaited.

    Args:
        temp_db_path: Non-None filesystem path string for the temporary SQLite
            database. The path must be valid for creating/opening the test
            database.

    Returns:
        SyncBot: The initialized bot instance (awaited result) with schema
        created and bans loaded from the database.
    """
    bot = SyncBot(temp_db_path)
    await bot.setup_db()
    await bot.load_bans_from_db()
    return bot


@pytest.fixture
def sync_module():
    """Return the sync module for tests that need NICK or asyncio hooks."""
    import banbot.sync as module

    return module


@asynccontextmanager
async def admin_room_override(sync_module, value: str = TEST_ADMIN_ROOM) -> AsyncIterator[None]:
    """Temporarily override sync.ADMIN_ROOM for a single async test scope."""
    original = sync_module.ADMIN_ROOM
    sync_module.ADMIN_ROOM = value
    try:
        yield
    finally:
        sync_module.ADMIN_ROOM = original


@pytest.mark.asyncio
async def test_sync_bans_to_rooms_applies_only_missing_bans(temp_db_path, sync_module):
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
        assert len(bot.applied) == 1
        assert all(applied[1] != "already@example.test" for applied in bot.applied)
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
        await bot.upsert_ban_db(
            "expired@example.test",
            None,
            int(time.time()) - EXPIRY_THRESHOLD_SECONDS,
            "tester",
            "expired",
        )
        await bot.sync_bans_to_rooms_for_single_room("room@conference.example.test")

        assert bot.unbanned == [("expired@example.test", "system")]
        await bot.load_bans_from_db()
        assert "expired@example.test" not in bot.ban_index_by_jid
    finally:
        await bot.db.close()


@pytest.mark.asyncio
async def test_sync_bans_to_rooms_skips_room_without_bot_admin_rights(temp_db_path, sync_module):
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
    service = FakeMucService(TEST_ROOM_OWNER_ADMIN_NESTED)

    assert service.affiliations == TEST_ROOM_OWNER_ADMIN_FLAT
    assert await service.get_users_by_affiliation(TEST_ROOM, "owner") == [TEST_OWNER_JID]
    assert await service.get_users_by_affiliation(TEST_ROOM, "admin") == [TEST_ADMIN_JID]
    assert await service.get_users_by_affiliation(TEST_ROOM, "outcast") == []
    assert await service.get_users_by_affiliation("missing-room@example.test", "owner") == []


@pytest.mark.asyncio
async def test_fake_muc_service_accepts_tuple_affiliation_mapping():
    service = FakeMucService(TEST_ROOM_OWNER_ADMIN_FLAT)

    assert service.affiliations == TEST_ROOM_OWNER_ADMIN_FLAT
    assert await service.get_users_by_affiliation(TEST_ROOM, "owner") == [TEST_OWNER_JID]
    assert await service.get_users_by_affiliation(TEST_ROOM, "admin") == [TEST_ADMIN_JID]


def test_fake_muc_service_handles_empty_affiliation_mapping():
    assert FakeMucService().affiliations == {}
    assert FakeMucService({}).affiliations == {}


@pytest.mark.asyncio
async def test_sync_admins_populates_owner_and_admin_occupants(temp_db_path, sync_module):
    bot = await make_bot(temp_db_path)
    muc_service = FakeMucService(TEST_ADMIN_ROOM_OWNER_ADMIN_FLAT)
    bot.plugin["xep_0045"] = muc_service
    try:
        async with admin_room_override(sync_module):
            await bot.sync_admins(announce=True)

        occupants = bot.occupants[TEST_ADMIN_ROOM]
        assert occupants[TEST_OWNER_JID]["affiliation"] == "owner"
        assert occupants[TEST_ADMIN_JID]["affiliation"] == "admin"
        assert {
            (TEST_ADMIN_ROOM, "owner"),
            (TEST_ADMIN_ROOM, "admin"),
        } <= set(muc_service.calls)
        assert "Current admins/owners" in bot.sent[-1]["mbody"]
    finally:
        await bot.db.close()


@pytest.mark.asyncio
async def test_sync_admins_refreshes_admin_occupants_via_public_api(temp_db_path, sync_module):
    bot = await make_bot(temp_db_path)
    muc_service = FakeMucService(TEST_ADMIN_ROOM_OWNER_ADMIN_NESTED)
    bot.plugin["xep_0045"] = muc_service
    try:
        bot.admin_rooms = {TEST_ADMIN_ROOM}

        async with admin_room_override(sync_module):
            await bot.sync_admins(announce=True)

        assert TEST_ADMIN_ROOM in bot.occupants
        assert bot.occupants[TEST_ADMIN_ROOM][TEST_OWNER_JID]["affiliation"] == "owner"

        muc_service.affiliations = muc_service._normalize_affiliations(
            {TEST_ADMIN_ROOM: {"owner": [TEST_NEW_OWNER_JID], "admin": []}}
        )
        bot.occupants = {}

        async with admin_room_override(sync_module):
            await bot.sync_admins(announce=True)

        refreshed_occupants = bot.occupants[TEST_ADMIN_ROOM]
        assert TEST_NEW_OWNER_JID in refreshed_occupants
        assert TEST_OWNER_JID not in refreshed_occupants
        assert TEST_ADMIN_JID not in refreshed_occupants
    finally:
        await bot.db.close()


@pytest.mark.asyncio
async def test_sync_rooms_and_bans_reports_empty_room_set(temp_db_path, sync_module):
    bot = await make_bot(temp_db_path)
    bot.protected_rooms = set()
    try:
        await bot.sync_rooms_and_bans()

        assert bot.sent[-1]["mbody"] == "⚠️ No protected rooms to sync."
    finally:
        await bot.db.close()


@pytest.mark.asyncio
async def test_sync_rooms_and_bans_uses_configured_batch_size(temp_db_path, monkeypatch, sync_module):
    monkeypatch.setattr(sync_module.asyncio, "sleep", noop_sleep)
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


async def create_and_configure_bot_for_room_sync(
    temp_db_path,
    sync_module,
    room,
    *,
    fail_join=False,
) -> SyncBot:
    """Create and configure a SyncBot for a room-sync test scenario.

    Args:
        temp_db_path: Path to the temporary SQLite database used by the bot.
        sync_module: Imported sync module providing constants used by the bot.
        room: Room JID to configure as both protected and admin room.
        fail_join: Whether the fake MUC service should simulate join failure
            for ``room``.

    Returns:
        A configured bot instance ready to run ``sync_rooms_and_bans`` for the
        given room scenario.
    """
    bot = await make_bot(temp_db_path)
    bot.protected_rooms = {room}
    bot.admin_rooms = {room}
    bot.occupants = {room: {sync_module.NICK: {"jid": "bot@example.test"}}}
    failed_rooms = {room} if fail_join else None
    bot.plugin["xep_0045"] = FakeMucService(fail_join_rooms=failed_rooms)
    return bot


@pytest.mark.asyncio
async def test_sync_rooms_no_join_time_on_fail(temp_db_path, monkeypatch, sync_module):
    monkeypatch.setattr(sync_module.asyncio, "sleep", noop_sleep)
    room = "room@conference.example.test"
    bot = await create_and_configure_bot_for_room_sync(
        temp_db_path, sync_module, room, fail_join=True
    )
    try:
        await bot.sync_rooms_and_bans()

        assert room not in bot.room_join_time
        assert bot.bot_admin_state[room] is True
    finally:
        await bot.db.close()


@pytest.mark.asyncio
async def test_sync_rooms_sets_join_time_on_success(temp_db_path, monkeypatch, sync_module):
    monkeypatch.setattr(sync_module.asyncio, "sleep", noop_sleep)
    room = "room-ok@conference.example.test"
    bot = await create_and_configure_bot_for_room_sync(temp_db_path, sync_module, room)
    try:
        await bot.sync_rooms_and_bans()

        assert room in bot.room_join_time
    finally:
        await bot.db.close()
