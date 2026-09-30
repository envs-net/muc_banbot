"""Compatibility facade over envs-xmpp task supervision."""
from __future__ import annotations

import asyncio
from typing import Any

from envs_xmpp_core.runtime import TaskInfo
from envs_xmpp_core.runtime.tasks import SupervisorOptions
from envs_xmpp_core.runtime.tasks import TaskSupervisor as CoreTaskSupervisor
from envs_xmpp_core.runtime.tasks import sleep_with_heartbeat as _core_sleep_with_heartbeat


def _stale_after(owner: Any) -> float:
    supervisor = getattr(owner, "tasks", None)
    options = getattr(supervisor, "options", None)
    try:
        return float(getattr(options, "stale_after", 3600.0) or 3600.0)
    except (TypeError, ValueError):
        return 3600.0


async def sleep_with_heartbeat(
    owner: Any,
    name: str,
    delay: float,
    *,
    group: str = "_core",
    interval: float = 3600.0,
    sleep_func=None,
) -> None:
    """Sleep while keeping one supervised BanBot service heartbeat fresh."""
    sleeper = sleep_func or asyncio.sleep
    if float(delay) <= 0:
        await sleeper(0)
        return
    supervisor = getattr(owner, "tasks", None)
    heartbeat = getattr(supervisor, "heartbeat", None)
    callback = None
    if callable(heartbeat):
        callback = lambda: heartbeat(group, name)
    await _core_sleep_with_heartbeat(
        delay,
        heartbeat=callback,
        stale_after=_stale_after(owner),
        interval=interval,
        sleep_func=sleeper,
    )


class TaskSupervisor(CoreTaskSupervisor):
    """BanBot group-oriented compatibility API backed by the neutral core."""

    def __init__(self) -> None:
        super().__init__(
            SupervisorOptions(
                max_restarts=2**31 - 1,
                initial_backoff=1.0,
                max_backoff=60.0,
                reset_after=0.0,
                stale_after=3600.0,
                yield_before_start=False,
                terminal_error_style="restart_limit",
            )
        )

    def create(
        self,
        group: str,
        coro,
        *,
        name: str | None = None,
        kind: str = "one-shot",
    ) -> asyncio.Task[Any]:
        """Create a task for a BanBot group backed by the shared scope model."""
        return super().create(group, coro, name=name, kind=kind)

    def create_resilient(
        self,
        group: str,
        factory,
        *,
        name: str,
        max_restarts: int | None = None,
        service: bool = True,
    ) -> asyncio.Task[Any]:
        return super().create_resilient(
            group,
            factory,
            name=name,
            max_restarts=(2**31 - 1 if max_restarts is None else max_restarts),
            initial_backoff=1.0,
            max_backoff=60.0,
            reset_after=0.0,
            service=service,
        )

    async def cancel_group(self, group: str, *, timeout: float = 5.0) -> int:
        return await super().cancel_scope(group, timeout=timeout)

    def stale_services(self, max_age_seconds: float) -> list[TaskInfo]:
        """Return stale service tasks using the canonical shared snapshot model."""
        return [
            info
            for info in super().stale_tasks(max_age_seconds=max_age_seconds)
            if info.kind == "service"
        ]
