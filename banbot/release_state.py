"""Adapters for shared persistent release-state storage."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

import aiosqlite
from envs_xmpp_core.release.state import ReleaseState, ReleaseStateSqlRepository

if TYPE_CHECKING:
    from .contracts import ReleaseStateHost

log = logging.getLogger(__name__)

_LEGACY_LAST_STARTED_VERSION_KEY = "last_successful_start_version"


class BanBotReleaseStateSqlBackend:
    """Adapt BanBot's aiosqlite connection to the shared release-state repository."""

    def __init__(self, bot: ReleaseStateHost) -> None:
        self.bot = bot

    def _connection(self) -> aiosqlite.Connection | None:
        """Snapshot the current connection once for one repository operation."""
        return self.bot.db

    def available(self) -> bool:
        return self._connection() is not None

    async def execute(
        self,
        query: str,
        params: Sequence[Any] = (),
        *,
        label: str = "release_state",
    ) -> int:
        del label
        db = self._connection()
        if db is None:
            raise RuntimeError("release state database is unavailable")
        cursor = await db.execute(query, tuple(params))
        await db.commit()
        rowcount = cursor.rowcount
        return rowcount if rowcount is not None and rowcount >= 0 else 0

    async def fetch_one(
        self,
        query: str,
        params: Sequence[Any] = (),
    ) -> Sequence[Any] | None:
        db = self._connection()
        if db is None:
            return None
        async with db.execute(query, tuple(params)) as cursor:
            return await cursor.fetchone()


def release_state_repository(bot: ReleaseStateHost) -> ReleaseStateSqlRepository:
    """Return the shared release-state repository for one BanBot instance."""
    return ReleaseStateSqlRepository(BanBotReleaseStateSqlBackend(bot))


async def _remove_legacy_version_metadata(bot: ReleaseStateHost) -> bool:
    """Best-effort removal of the obsolete startup-version metadata row."""
    db = bot.db
    if db is None:
        return False
    try:
        cursor = await db.execute(
            "DELETE FROM bot_metadata WHERE key = ?",
            (_LEGACY_LAST_STARTED_VERSION_KEY,),
        )
        await db.commit()
    except Exception as exc:
        log.warning("Could not remove migrated legacy startup version metadata: %s", exc)
        return False
    return bool(cursor.rowcount and cursor.rowcount > 0)


async def load_release_state_with_legacy_migration(
    bot: ReleaseStateHost,
    repository: ReleaseStateSqlRepository,
) -> ReleaseState:
    """Load shared state and migrate BanBot's historical metadata key once.

    A stale legacy row is also cleaned up when shared state already exists.  This
    covers an interrupted/failed cleanup after the release-state row itself was
    committed on an earlier start.
    """
    state = await repository.load()
    if state.version is not None or state.pending_announcement is not None:
        if await _remove_legacy_version_metadata(bot):
            log.info("Removed stale legacy startup release-state metadata")
        return state

    db = bot.db
    if db is None:
        return state

    try:
        async with db.execute(
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
    if await _remove_legacy_version_metadata(bot):
        log.info("Migrated startup release state to shared release_state storage")
    return migrated
