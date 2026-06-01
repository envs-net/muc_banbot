"""Shared runtime locks used across mixins."""

from __future__ import annotations

import asyncio
import weakref
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator


_FALLBACK_DATABASE_FILE_LOCKS: weakref.WeakKeyDictionary[Any, asyncio.Lock] = weakref.WeakKeyDictionary()
_FALLBACK_BAN_STATE_LOCKS: weakref.WeakKeyDictionary[Any, asyncio.Lock] = weakref.WeakKeyDictionary()


def is_maintenance_mode(owner: Any) -> bool:
    """Return True while a high-impact DB mutation is active."""
    return int(getattr(owner, "_maintenance_operation_depth", 0) or 0) > 0


@asynccontextmanager
async def maintenance_operation(owner: Any) -> AsyncIterator[None]:
    """Mark the bot as running a maintenance operation for background workers."""
    owner._maintenance_operation_depth = int(getattr(owner, "_maintenance_operation_depth", 0) or 0) + 1
    try:
        yield
    finally:
        owner._maintenance_operation_depth = max(0, int(getattr(owner, "_maintenance_operation_depth", 0) or 0) - 1)


def _fallback_lock(
    registry: weakref.WeakKeyDictionary[Any, asyncio.Lock],
    owner: Any,
    attr_name: str,
) -> asyncio.Lock:
    """Return a per-object fallback lock for lightweight test/mixin objects.

    The real BanBot initializes shared locks centrally.  Some tests instantiate
    individual mixins without running BanBot.__init__(), so they do not have the
    lock attributes.  A weak fallback keeps those objects usable without
    reintroducing duplicate lock attributes on production mixins.
    """
    try:
        return registry[owner]
    except KeyError:
        lock = asyncio.Lock()
        registry[owner] = lock
        return lock
    except TypeError:
        # Very defensive fallback for objects that cannot be weak-referenced.
        # This path is not used by BanBot, but keeps standalone mixin users safe.
        lock = getattr(owner, attr_name, None)
        if lock is None:
            lock = asyncio.Lock()
            setattr(owner, attr_name, lock)
        return lock


def get_database_file_lock(owner: Any) -> asyncio.Lock:
    """Return the shared lock for database/config/export file operations."""
    lock = getattr(owner, "_database_file_operation_lock", None)
    if lock is not None:
        return lock
    return _fallback_lock(_FALLBACK_DATABASE_FILE_LOCKS, owner, "_database_file_operation_lock")


def get_ban_state_lock(owner: Any) -> asyncio.Lock:
    """Return the shared lock for DB-backed ban/cache state changes."""
    lock = getattr(owner, "_ban_state_operation_lock", None)
    if lock is not None:
        return lock
    return _fallback_lock(_FALLBACK_BAN_STATE_LOCKS, owner, "_ban_state_operation_lock")


@asynccontextmanager
async def database_file_lock(owner: Any) -> AsyncIterator[None]:
    """Acquire the database-file lock with same-task reentrancy.

    The lock itself is still initialized centrally on the bot/test object.  The
    small owner/depth bookkeeping only prevents deadlocks when a high-level
    operation already holding the lock calls a lower-level helper that also
    protects database/config/export files.
    """
    task = asyncio.current_task()
    if getattr(owner, "_database_file_lock_owner_task", None) is task:
        owner._database_file_lock_depth = getattr(owner, "_database_file_lock_depth", 0) + 1
        try:
            yield
        finally:
            owner._database_file_lock_depth -= 1
        return

    async with get_database_file_lock(owner):
        owner._database_file_lock_owner_task = task
        owner._database_file_lock_depth = 1
        try:
            yield
        finally:
            owner._database_file_lock_depth = 0
            owner._database_file_lock_owner_task = None


@asynccontextmanager
async def ban_state_lock(owner: Any) -> AsyncIterator[None]:
    """Acquire the ban-state lock with same-task reentrancy."""
    task = asyncio.current_task()
    if getattr(owner, "_ban_state_lock_owner_task", None) is task:
        owner._ban_state_lock_depth = getattr(owner, "_ban_state_lock_depth", 0) + 1
        try:
            yield
        finally:
            owner._ban_state_lock_depth -= 1
        return

    async with get_ban_state_lock(owner):
        owner._ban_state_lock_owner_task = task
        owner._ban_state_lock_depth = 1
        try:
            yield
        finally:
            owner._ban_state_lock_depth = 0
            owner._ban_state_lock_owner_task = None


@asynccontextmanager
async def database_mutation_locks(owner: Any) -> AsyncIterator[None]:
    """Acquire mutation locks in canonical order and mark maintenance mode."""
    async with maintenance_operation(owner):
        async with database_file_lock(owner):
            async with ban_state_lock(owner):
                yield
