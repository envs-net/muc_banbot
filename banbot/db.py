"""SQLite schema setup, ban persistence, migrations, and ban CRUD helpers."""

import logging
import time

import aiosqlite
from config import DB_FILE

from .utils import normalize_ban_target

log = logging.getLogger(__name__)


class DatabaseMixin:
    async def setup_db(self) -> None:
        """Initialize SQLite DB, migrate bans schema, create indexes, load rooms."""
        self.db = await aiosqlite.connect(DB_FILE)
        await self.db.execute("PRAGMA foreign_keys = ON")

        async with self.db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='bans'"
        ) as cursor:
            bans_table_exists = await cursor.fetchone()

        if not bans_table_exists:
            await self.db.execute("""
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
            """)
        else:
            async with self.db.execute("PRAGMA table_info(bans)") as cursor:
                columns = [r[1] async for r in cursor]

            if "target_type" not in columns or "target" not in columns:
                log.info("DB migration: migrating bans table to target_type/target schema")
                await self.db.execute("ALTER TABLE bans RENAME TO bans_old")
                await self.db.execute("""
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
                """)

                legacy_select = (
                    f"SELECT "
                    f"{('jid' if 'jid' in columns else 'NULL')} AS jid, "
                    f"{('nick' if 'nick' in columns else 'NULL')} AS nick, "
                    f"{('until' if 'until' in columns else '0')} AS until, "
                    f"{('issuer' if 'issuer' in columns else 'NULL')} AS issuer, "
                    f"{('comment' if 'comment' in columns else 'NULL')} AS comment "
                    f"FROM bans_old"
                )
                async with self.db.execute(legacy_select) as cursor:
                    old_rows = await cursor.fetchall()

                migrated: dict[tuple[str, str], tuple[str, str, str | None, str | None, int, str | None, str | None]] = {}
                for jid, nick, until, issuer, comment in old_rows:
                    try:
                        target_type, target, normalized_jid, normalized_nick = normalize_ban_target(jid, nick)
                        key = (target_type, target)
                        previous = migrated.get(key)
                        if previous and previous[4] <= 0:
                            continue
                        migrated[key] = (
                            target_type,
                            target,
                            normalized_jid,
                            normalized_nick,
                            int(until or 0),
                            issuer,
                            comment,
                        )
                    except Exception as e:
                        log.warning(
                            "Skipping invalid legacy ban row during migration: jid=%r nick=%r error=%s",
                            jid,
                            nick,
                            e,
                        )

                if migrated:
                    await self.db.executemany(
                        """
                        INSERT OR REPLACE INTO bans
                            (target_type, target, jid, nick, until, issuer, comment)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        list(migrated.values()),
                    )

                await self.db.execute("DROP TABLE bans_old")
                log.info("DB migration complete: %d ban rows migrated", len(migrated))

        await self.db.execute("""
            CREATE TABLE IF NOT EXISTS rooms (
                room TEXT PRIMARY KEY
            )
        """)

        await self.db.execute("""
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
        """)

        await self.db.execute("""
            CREATE TABLE IF NOT EXISTS redaction_index (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                room_jid TEXT NOT NULL,
                sender_jid TEXT NOT NULL,
                sender_nick TEXT,
                stanza_id TEXT NOT NULL,
                message_id TEXT,
                created_at INTEGER NOT NULL DEFAULT (strftime('%s','now')),
                redacted_at INTEGER,
                redacted_by TEXT,
                redact_reason TEXT,
                UNIQUE(room_jid, stanza_id)
            )
        """)

        await self.db.execute("""
            CREATE TABLE IF NOT EXISTS public_policy (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                enabled INTEGER NOT NULL DEFAULT 0,
                text TEXT NOT NULL DEFAULT '',
                updated_at INTEGER NOT NULL DEFAULT (strftime('%s','now'))
            )
        """)

        await self.db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_bans_target ON bans(target_type, target)")
        await self.db.execute("CREATE INDEX IF NOT EXISTS idx_bans_jid ON bans(jid)")
        await self.db.execute("CREATE INDEX IF NOT EXISTS idx_bans_nick ON bans(LOWER(nick))")
        await self.db.execute("CREATE INDEX IF NOT EXISTS idx_bans_until ON bans(until)")
        await self.db.execute("CREATE INDEX IF NOT EXISTS idx_bans_domain ON bans(target) WHERE target_type = 'domain'")
        await self.db.execute("CREATE INDEX IF NOT EXISTS idx_audit_created_at ON audit_log(created_at)")
        await self.db.execute("CREATE INDEX IF NOT EXISTS idx_audit_event_type ON audit_log(event_type)")
        await self.db.execute("CREATE INDEX IF NOT EXISTS idx_audit_actor ON audit_log(actor)")
        await self.db.execute("CREATE INDEX IF NOT EXISTS idx_audit_target ON audit_log(target_type, target)")
        await self.db.execute("CREATE INDEX IF NOT EXISTS idx_redaction_sender ON redaction_index(sender_jid)")
        await self.db.execute("CREATE INDEX IF NOT EXISTS idx_redaction_room ON redaction_index(room_jid)")
        await self.db.execute("CREATE INDEX IF NOT EXISTS idx_redaction_created_at ON redaction_index(created_at)")
        await self.db.execute("CREATE INDEX IF NOT EXISTS idx_redaction_redacted_at ON redaction_index(redacted_at)")
        await self.db.execute("""
            CREATE INDEX IF NOT EXISTS idx_redaction_lookup_active
            ON redaction_index(sender_jid, room_jid, created_at, id)
            WHERE redacted_at IS NULL
        """)
        await self.db.commit()
        log.info("✅ Database schema and indexes created/verified")

        async with self.db.execute("SELECT room FROM rooms") as cursor:
            rows = await cursor.fetchall()
            for (room,) in rows:
                self.protected_rooms.add(room)


    async def load_bans_from_db(self) -> None:
        """
        Load all active bans from the database into RAM cache with O(1) lookup indexes.
        """
        now = int(time.time())
        async with self.db.execute(
            """
            SELECT target_type, target, jid, nick, until, issuer, comment
            FROM bans
            WHERE until = 0 OR until > ?
            """,
            (now,),
        ) as cursor:
            rows = await cursor.fetchall()

        self.ban_cache.clear()
        self.ban_index_by_jid.clear()
        self.ban_index_by_nick.clear()
        self.ban_index_by_domain.clear()

        for target_type, target, jid, nick, until, issuer, comment in rows:
            if target_type == "domain" and not jid:
                jid = f"*.{target}"
            self._cache_ban(jid, nick, int(until or 0), issuer, comment)

        log.info("✅ Loaded %d active bans", len(self.ban_cache))


    async def find_active_jid_ban_by_nick(
        self,
        nick: str | None,
    ) -> tuple[str | None, int | None, str | None, str | None] | None:
        """Return an active JID ban row for a nick, if one already exists.

        This prevents creating a second nick-only ban for a user who already has
        a JID-based ban with the same stored nick.
        """
        if not nick:
            return None

        normalized_nick = nick.lower().strip()
        now = int(time.time())

        async with self.db.execute(
            """
            SELECT jid, until, issuer, comment
            FROM bans
            WHERE target_type = 'jid'
              AND LOWER(nick) = ?
              AND (until = 0 OR until > ?)
            ORDER BY CASE WHEN until = 0 THEN 0 ELSE 1 END, until DESC
            LIMIT 1
            """,
            (normalized_nick, now),
        ) as cursor:
            row = await cursor.fetchone()

        if not row:
            return None

        jid, until, issuer, comment = row
        bare_jid = self.bare_jid(jid) if jid else None
        if not bare_jid:
            return None

        return bare_jid, int(until or 0), issuer, comment


    async def delete_duplicate_nick_ban(
        self,
        nick: str | None,
    ) -> int:
        """Delete a nick-only ban after it has been merged into a JID ban."""
        if not nick:
            return 0

        normalized_nick = nick.lower().strip()
        cur = await self.db.execute(
            "DELETE FROM bans WHERE target_type = 'nick' AND target = ?",
            (normalized_nick,),
        )
        deleted = cur.rowcount

        if deleted:
            self._remove_ban_from_cache(normalized_nick, ban_nick=normalized_nick)
            log.info("🧹 Removed duplicate nick-only ban for %s after JID merge", normalized_nick)

        return deleted


    async def upsert_ban_db(
        self,
        jid: str | None,
        nick: str | None,
        until: int,
        issuer: str | None,
        comment: str | None,
    ) -> None:
        """Insert or update a ban using the normalized target_type/target key."""
        target_type, target, normalized_jid, normalized_nick = normalize_ban_target(jid, nick)
        if target_type == "domain" and normalized_jid is None:
            normalized_jid = f"*.{target}"

        await self.db.execute(
            """
            INSERT INTO bans (target_type, target, jid, nick, until, issuer, comment, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, strftime('%s','now'))
            ON CONFLICT(target_type, target) DO UPDATE SET
                jid = excluded.jid,
                nick = excluded.nick,
                until = excluded.until,
                issuer = excluded.issuer,
                comment = excluded.comment,
                updated_at = strftime('%s','now')
            """,
            (target_type, target, normalized_jid, normalized_nick, until, issuer, comment),
        )
        if target_type == "jid" and normalized_nick:
            await self.delete_duplicate_nick_ban(normalized_nick)

        await self.db.commit()
        self._cache_ban(normalized_jid, normalized_nick, until, issuer, comment)


    async def delete_ban_db(self, identifier: str) -> int:
        """Delete a ban by JID, nick, or wildcard domain and return the affected row count."""
        ident = identifier.lower().strip()

        if ident.startswith("*."):
            target_type = "domain"
            target = ident[2:].strip(".")
        elif "@" in ident:
            target_type = "jid"
            target = self.bare_jid(ident)
        else:
            target_type = "nick"
            target = ident

        cur = await self.db.execute(
            "DELETE FROM bans WHERE target_type = ? AND target = ?",
            (target_type, target),
        )
        await self.db.commit()
        return cur.rowcount


    async def get_public_policy(self) -> tuple[bool, str]:
        """Return public policy enabled state and text."""
        async with self.db.execute(
            "SELECT enabled, text FROM public_policy WHERE id = 1"
        ) as cursor:
            row = await cursor.fetchone()

        if not row:
            return False, ""

        return bool(row[0]), row[1] or ""


    async def set_public_policy_text(self, text: str, enabled: bool = True) -> None:
        """Persist public policy text and optionally enable it."""
        await self.db.execute(
            """
            INSERT INTO public_policy (id, enabled, text, updated_at)
            VALUES (1, ?, ?, strftime('%s','now'))
            ON CONFLICT(id)
            DO UPDATE SET
                enabled = excluded.enabled,
                text = excluded.text,
                updated_at = strftime('%s','now')
            """,
            (1 if enabled else 0, text),
        )
        await self.db.commit()


    async def set_public_policy_enabled(self, enabled: bool) -> None:
        """Enable or disable the public policy command."""
        _current_enabled, text = await self.get_public_policy()

        await self.db.execute(
            """
            INSERT INTO public_policy (id, enabled, text, updated_at)
            VALUES (1, ?, ?, strftime('%s','now'))
            ON CONFLICT(id)
            DO UPDATE SET
                enabled = excluded.enabled,
                updated_at = strftime('%s','now')
            """,
            (1 if enabled else 0, text),
        )
        await self.db.commit()


    async def clear_public_policy(self) -> None:
        """Clear and disable the public policy text."""
        await self.db.execute(
            """
            INSERT INTO public_policy (id, enabled, text, updated_at)
            VALUES (1, 0, '', strftime('%s','now'))
            ON CONFLICT(id)
            DO UPDATE SET
                enabled = 0,
                text = '',
                updated_at = strftime('%s','now')
            """
        )
        await self.db.commit()
