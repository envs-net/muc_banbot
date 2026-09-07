"""Adapters for shared persistent release-state storage."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

from envs_xmpp_core.release.state import ReleaseState, ReleaseStateSqlRepository

log = logging.getLogger(__name__)

_LEGACY_LAST_STARTED_VERSION_KEY = "last_successful_start_version"


class BanBotReleaseStateSqlBackend:
    """Adapt BanBot's aiosqlite connection to the shared release-state repository."""

    def __init__(self, bot: Any) -> None:
        self.bot = bot

    def available(self) -> bool:
        return getattr(self.bot, "db", None) is not None

    async def execute(
        self,
        query: str,
        params: Sequence[Any] = (),
        *,
        label: str = "release_state",
    ) -> int:
        del label
        if not self.available():
            raise RuntimeError("release state database is unavailable")
        cursor = await self.bot.db.execute(query, tuple(params))
        await self.bot.db.commit()
        rowcount = cursor.rowcount
        return rowcount if rowcount is not None and rowcount >= 0 else 0

    async def fetch_one(self, query: str, params: Sequence[Any] = ()):
        if not self.available():
            return None
        async with self.bot.db.execute(query, tuple(params)) as cursor:
            return await cursor.fetchone()


def release_state_repository(bot: Any) -> ReleaseStateSqlRepository:
    return ReleaseStateSqlRepository(BanBotReleaseStateSqlBackend(bot))


async def load_release_state_with_legacy_migration(
    bot: Any,
    repository: ReleaseStateSqlRepository,
) -> ReleaseState:
    """Load shared state and migrate BanBot's historical metadata key once."""
    state = await repository.load()
    if state.version is not None or state.pending_announcement is not None:
        return state

    try:
        async with bot.db.execute(
            "SELECT value FROM bot_metadata WHERE key = ?",
            (_LEGACY_LAST_STARTED_VERSION_KEY,),
        ) as cursor:
            row = await cursor.fetchone()
    except Exception as exc:
        log.debug("Could not inspect legacy startup version metadata: %s", exc)
        return state

    if not row or not row[0]:
        return state

    migrated = ReleaseState(version=row[0])
    await repository.save(migrated)
    try:
        await bot.db.execute(
            "DELETE FROM bot_metadata WHERE key = ?",
            (_LEGACY_LAST_STARTED_VERSION_KEY,),
        )
        await bot.db.commit()
    except Exception as exc:
        log.warning("Could not remove migrated legacy startup version metadata: %s", exc)
    else:
        log.info("Migrated startup release state to shared release_state storage")
    return migrated
