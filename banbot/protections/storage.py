"""Persistence helpers for protection configuration."""

from __future__ import annotations

import json
import logging
import time
from typing import TYPE_CHECKING

from .definitions import canonical_protection_name

log = logging.getLogger(__name__)


if TYPE_CHECKING:
    from ..contracts import ProtectionStorageMixinHost

    class _ProtectionStorageMixinContract(ProtectionStorageMixinHost):
        pass
else:
    class _ProtectionStorageMixinContract:
        pass


class ProtectionStorageMixin(_ProtectionStorageMixinContract):
    async def setup_protections_db(self) -> None:
        """Create persistence table for protection overrides."""
        db = getattr(self, "db", None)
        if db is None:
            return
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS protections (
                name TEXT PRIMARY KEY,
                enabled INTEGER NOT NULL,
                config_json TEXT NOT NULL DEFAULT '{}',
                updated_at INTEGER NOT NULL DEFAULT (strftime('%s','now'))
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS protection_known_participants (
                room TEXT NOT NULL,
                jid TEXT NOT NULL,
                first_seen_at INTEGER NOT NULL DEFAULT (strftime('%s','now')),
                PRIMARY KEY (room, jid)
            )
            """
        )
        await db.commit()
        self._protection_storage_ready = True

    async def load_protections(self) -> None:
        """Load protection enabled state/config overrides from SQLite."""
        self.init_protection_state()
        db = getattr(self, "db", None)
        if db is None:
            return
        await self.setup_protections_db()
        async with db.execute("SELECT name, enabled, config_json FROM protections") as cursor:
            rows = await cursor.fetchall()

        for raw_name, enabled, config_json in rows:
            name = canonical_protection_name(str(raw_name)) or str(raw_name)
            if name not in self.protections:
                log.warning("Ignoring unknown persisted protection: %s", raw_name)
                continue
            config = dict(self.protections[name])
            try:
                loaded = json.loads(config_json or "{}")
            except json.JSONDecodeError:
                loaded = {}
            if isinstance(loaded, dict):
                config.update(loaded)
            config["enabled"] = bool(enabled)
            self.protections[name] = config

        async with db.execute(
            "SELECT room, jid FROM protection_known_participants"
        ) as cursor:
            known_rows = await cursor.fetchall()
        for room, jid in known_rows:
            key = self._protection_participant_key(str(room), str(jid))
            self._protection_mark_participant_known(*key)
            self.protection_persisted_known_participants.add(key)

    async def remember_protection_participant(
        self,
        room: str,
        subject: str,
        *,
        persistent: bool,
    ) -> None:
        """Remember a participant for protections that distinguish established users.

        Only verified bare JIDs are remembered across joins.  Nick-only identities
        intentionally stay session-local via ``protection_first_message_seen``; a
        later occupant can reuse the same nick and must not inherit trust.
        """
        if not persistent:
            return

        key = self._protection_participant_key(room, subject)
        self._protection_mark_participant_known(*key)
        if key in self.protection_persisted_known_participants:
            return

        db = getattr(self, "db", None)
        if db is None:
            return

        if not getattr(self, "_protection_storage_ready", False):
            await self.setup_protections_db()
        await db.execute(
            """
            INSERT OR IGNORE INTO protection_known_participants
                (room, jid, first_seen_at)
            VALUES (?, ?, ?)
            """,
            (
                key[0],
                key[1],
                int(time.time()),
            ),
        )
        await db.commit()
        self.protection_persisted_known_participants.add(key)

    async def persist_protection(self, name: str) -> None:
        """Persist one protection config override."""
        db = getattr(self, "db", None)
        if db is None:
            return
        await self.setup_protections_db()
        config = dict(self.protections[name])
        enabled = bool(config.pop("enabled", False))
        await db.execute(
            """
            INSERT INTO protections (name, enabled, config_json, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(name) DO UPDATE SET
                enabled = excluded.enabled,
                config_json = excluded.config_json,
                updated_at = excluded.updated_at
            """,
            (name, 1 if enabled else 0, json.dumps(config, sort_keys=True), int(time.time())),
        )
        await db.commit()
