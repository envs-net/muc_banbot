"""Compatibility helpers for tracked Slixmpp MUC joins."""

from __future__ import annotations

import asyncio
import inspect


def start_muc_join_task(
    muc_plugin,
    room: str,
    nick: str,
    *,
    timeout: float,
) -> tuple[asyncio.Future | asyncio.Task | None, str]:
    """Start the best available MUC join API and normalize it to a task."""
    join_wait = getattr(muc_plugin, "join_muc_wait", None)

    if callable(join_wait):
        try:
            result = join_wait(
                room,
                nick,
                maxstanzas=0,
                timeout=max(float(timeout), 0.1),
            )
        except TypeError:
            # Compatibility with lightweight fakes or older backports that do
            # not accept all current keyword arguments.
            result = join_wait(room, nick)
        api_name = "join_muc_wait"
    else:
        # Slixmpp >=1.8 provides join_muc_wait(). This legacy branch remains
        # only for test doubles and standalone compatibility users.
        result = muc_plugin.join_muc(room, nick)
        api_name = "join_muc"

    if asyncio.isfuture(result):
        return result, api_name
    if inspect.isawaitable(result):
        return asyncio.create_task(result), api_name
    return None, api_name


async def await_muc_join_compat(
    muc_plugin,
    room: str,
    nick: str,
    *,
    timeout: float,
) -> tuple[bool, str, Exception | None]:
    """Await a join for lightweight users without BanBot presence tracking."""
    try:
        task, api_name = start_muc_join_task(
            muc_plugin,
            room,
            nick,
            timeout=timeout,
        )
    except Exception as exc:
        return False, "unknown", exc

    if task is None:
        return True, api_name, None

    try:
        await asyncio.wait_for(task, timeout=max(float(timeout), 0.1))
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        if not task.done():
            task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            # Expected after cancelling the join waiter above; drain its result.
            pass
        except Exception:
            # Preserve the original join error and suppress cleanup-only failures.
            pass
        return False, api_name, exc

    return True, api_name, None
