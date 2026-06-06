import time

import pytest

aiosqlite = pytest.importorskip("aiosqlite")

from banbot.cache import CacheMixin
from banbot.db import DatabaseMixin


class DbBot(DatabaseMixin, CacheMixin):
    def __init__(self):
        self.protected_rooms = set()
        self.ban_cache = {}
        self.ban_index_by_jid = {}
        self.ban_index_by_nick = {}
        self.ban_index_by_domain = {}

    @staticmethod
    def bare_jid(jid: str) -> str:
        return str(jid).split("/", 1)[0].lower()


@pytest.mark.asyncio
async def test_setup_db_creates_schema_and_loads_rooms(temp_db_path):
    bot = DbBot()
    await bot.setup_db()
    try:
        async with bot.db.execute("SELECT name FROM sqlite_master WHERE type='table'") as cursor:
            tables = {row[0] for row in await cursor.fetchall()}
        assert {"bans", "rooms", "audit_log", "public_policy"}.issubset(tables)
    finally:
        await bot.db.close()


@pytest.mark.asyncio
async def test_upsert_load_and_delete_ban(temp_db_path):
    bot = DbBot()
    await bot.setup_db()
    try:
        await bot.upsert_ban_db(
            jid="User@Example.org/resource",
            nick="SomeNick",
            until=0,
            issuer="tester",
            comment="reason",
        )
        async with bot.db.execute("SELECT target, jid FROM bans") as cursor:
            rows = await cursor.fetchall()
        assert rows == [("user@example.org", "user@example.org")]

        await bot.load_bans_from_db()
        assert "user@example.org" in bot.ban_index_by_jid

        deleted = await bot.delete_ban_db("user@example.org")
        assert deleted == 1
    finally:
        await bot.db.close()


@pytest.mark.asyncio
async def test_find_active_jid_ban_by_nick_prefers_active(temp_db_path):
    bot = DbBot()
    await bot.setup_db()
    try:
        await bot.upsert_ban_db("old@example.org", "Nick", int(time.time()) - 1, "tester", "expired")
        await bot.upsert_ban_db("new@example.org", "Nick", 0, "tester", "permanent")
        result = await bot.find_active_jid_ban_by_nick("nick")
        assert result[0] == "new@example.org"
    finally:
        await bot.db.close()


@pytest.mark.asyncio
async def test_public_policy_roundtrip(temp_db_path):
    bot = DbBot()
    await bot.setup_db()
    try:
        assert await bot.get_public_policy() == (False, "")
        await bot.set_public_policy_text("Be nice", enabled=True)
        assert await bot.get_public_policy() == (True, "Be nice")
        await bot.set_public_policy_enabled(False)
        assert await bot.get_public_policy() == (False, "Be nice")
        await bot.clear_public_policy()
        assert await bot.get_public_policy() == (False, "")
    finally:
        await bot.db.close()


@pytest.mark.asyncio
async def test_setup_db_normalizes_existing_full_jid_bans(temp_db_path):
    db = await aiosqlite.connect(temp_db_path)
    try:
        await db.execute(
            """
            CREATE TABLE bans (
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
        await db.execute(
            """
            INSERT INTO bans (target_type, target, jid, nick, until, issuer, comment)
            VALUES ('jid', 'User@Example.org/resource', 'User@Example.org/resource', 'SomeNick', 0, 'tester', 'reason')
            """
        )
        await db.commit()
    finally:
        await db.close()

    bot = DbBot()
    await bot.setup_db()
    try:
        async with bot.db.execute("SELECT target, jid, nick FROM bans") as cursor:
            rows = await cursor.fetchall()

        assert rows == [("user@example.org", "user@example.org", "somenick")]

        await bot.load_bans_from_db()
        assert "user@example.org" in bot.ban_index_by_jid
        assert all("/" not in key for key in bot.ban_index_by_jid)
    finally:
        await bot.db.close()


@pytest.mark.asyncio
async def test_setup_db_deduplicates_full_and_bare_jid_bans(temp_db_path):
    db = await aiosqlite.connect(temp_db_path)
    try:
        await db.execute(
            """
            CREATE TABLE bans (
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
        await db.execute(
            """
            INSERT INTO bans (target_type, target, jid, nick, until, issuer, comment)
            VALUES ('jid', 'user@example.org/oldres', 'user@example.org/oldres', 'OldNick', 100, 'old', 'old temp')
            """
        )
        await db.execute(
            """
            INSERT INTO bans (target_type, target, jid, nick, until, issuer, comment)
            VALUES ('jid', 'user@example.org', 'user@example.org', 'NewNick', 0, 'new', 'permanent')
            """
        )
        await db.commit()
    finally:
        await db.close()

    bot = DbBot()
    await bot.setup_db()
    try:
        async with bot.db.execute("SELECT target, jid, nick, until, issuer, comment FROM bans") as cursor:
            rows = await cursor.fetchall()

        assert rows == [("user@example.org", "user@example.org", "newnick", 0, "new", "permanent")]
    finally:
        await bot.db.close()
