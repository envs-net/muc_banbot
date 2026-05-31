"""Shared runtime locks used across mixins."""

from __future__ import annotations

import asyncio
from typing import Any


def get_database_file_lock(owner: Any) -> asyncio.Lock:
    """Return the shared lock for database/config/export file operations."""
    return owner._database_file_operation_lock


def get_ban_state_lock(owner: Any) -> asyncio.Lock:
    """Return the shared lock for DB-backed ban/cache state changes."""
    return owner._ban_state_operation_lock
