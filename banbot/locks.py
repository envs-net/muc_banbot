"""Shared runtime locks used across mixins."""

from __future__ import annotations

import asyncio
from typing import Any


def get_database_file_lock(owner: Any) -> asyncio.Lock:
    """Return the shared lock for database/config/export file operations."""
    lock = getattr(owner, "_database_file_operation_lock", None)
    if lock is None:
        lock = asyncio.Lock()
        setattr(owner, "_database_file_operation_lock", lock)
    return lock


def get_ban_state_lock(owner: Any) -> asyncio.Lock:
    """Return the shared lock for DB-backed ban/cache state changes."""
    lock = getattr(owner, "_ban_state_operation_lock", None)
    if lock is None:
        lock = asyncio.Lock()
        setattr(owner, "_ban_state_operation_lock", lock)
    return lock
