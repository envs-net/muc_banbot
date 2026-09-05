"""Compatibility facade over envs-xmpp task supervision."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from envs_xmpp_core.runtime.tasks import SupervisorOptions
from envs_xmpp_core.runtime.tasks import TaskSupervisor as CoreTaskSupervisor


@dataclass(frozen=True)
class TaskInfo:
    """Stable operator-facing state for one supervised task."""

    group: str
    name: str
    status: str
    kind: str
    created_at: float
    heartbeat_at: float | None
    restart_count: int
    last_error: str | None
    restart_at: float | None


def _timestamp(value: str | None) -> float | None:
    if not value:
        return None
    return datetime.fromisoformat(value).timestamp()


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
        self._by_group = self._by_scope

    def create(
        self,
        group: str,
        coro,
        *,
        name: str | None = None,
        kind: str = "one-shot",
    ) -> asyncio.Task[Any]:
        """Create a core task while preserving BanBot's private metadata key."""
        task = super().create(group, coro, name=name, kind=kind)
        meta = self._tasks.get(task)
        if meta is not None:
            meta["group"] = group
        return task

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

    def snapshot(self, *, include_done: bool = True) -> list[TaskInfo]:
        result: list[TaskInfo] = []
        for item in super().snapshot(include_done=include_done):
            status = item.status
            # Preserve BanBot's operator-facing "restarting" status while the
            # shared core circuit is half-open and waiting for its next attempt.
            if status == "running" and item.circuit_state == "half-open":
                status = "restarting"
            result.append(
                TaskInfo(
                    group=item.scope,
                    name=item.name,
                    status=status,
                    kind=item.kind,
                    created_at=float(_timestamp(item.created_at) or 0.0),
                    heartbeat_at=_timestamp(item.heartbeat_at),
                    restart_count=item.restart_count,
                    last_error=item.last_error,
                    restart_at=_timestamp(item.next_restart_at),
                )
            )
        return sorted(result, key=lambda info: (info.group, info.name))

    def stale_services(self, max_age_seconds: float) -> list[TaskInfo]:
        now = datetime.now().timestamp()
        return [
            info
            for info in self.snapshot(include_done=False)
            if info.kind == "service"
            and info.status == "running"
            and info.heartbeat_at is not None
            and now - info.heartbeat_at > max_age_seconds
        ]
