"""Shared asyncio lock helpers for mixins."""

from __future__ import annotations

import asyncio
from typing import Any


def get_ban_state_lock(owner: Any) -> asyncio.Lock:
    """Return the shared ban-state lock, creating it when needed.

    Kept outside mixin classes so multiple mixins can share the same lock
    without defining conflicting base-class attributes.
    """
    lock = getattr(owner, "_ban_state_operation_lock", None)
    if lock is None:
        lock = asyncio.Lock()
        setattr(owner, "_ban_state_operation_lock", lock)
    return lock
