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
        await db.commit()

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
