"""Cancellation-safe helpers for managed blocking file operations."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any


async def wait_for_completion_on_cancel[T](awaitable: Awaitable[T]) -> T:
    """Await work to completion before propagating caller cancellation.

    ``asyncio.to_thread`` and worker-backed async APIs may keep running after
    the awaiting task is cancelled. Managed backup/restore/export code often
    owns temporary directories or serialization locks that must stay alive
    until that background work has actually stopped. This helper preserves
    those lifetime guarantees while still re-raising ``CancelledError``.
    """
    task = asyncio.ensure_future(awaitable)
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        try:
            await asyncio.shield(task)
        finally:
            raise


async def run_blocking_io[T](
    func: Callable[..., T],
    /,
    *args: Any,
    **kwargs: Any,
) -> T:
    """Run blocking file work without letting cancellation outlive its scope."""
    return await wait_for_completion_on_cancel(asyncio.to_thread(func, *args, **kwargs))
