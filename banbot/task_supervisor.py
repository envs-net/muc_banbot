"""Background task supervision for long-running BanBot workers."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import Any

log = logging.getLogger(__name__)


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


class TaskSupervisor:
    """Track and restart BanBot background services.

    BanBot has only a handful of core workers, so this intentionally keeps the
    contract smaller than envsbot's plugin supervisor while retaining the two
    important properties: one lifecycle owner and automatic recovery when a
    service coroutine exits unexpectedly.
    """

    def __init__(self) -> None:
        self._tasks: dict[asyncio.Task[Any], dict[str, Any]] = {}

    @staticmethod
    def _task_name(task: asyncio.Task[Any], fallback: str) -> str:
        get_name = getattr(task, "get_name", None)
        return str(get_name()) if callable(get_name) else fallback

    def create(
        self,
        group: str,
        coro: Coroutine[Any, Any, Any],
        *,
        name: str,
        kind: str = "one-shot",
    ) -> asyncio.Task[Any]:
        """Create and track one task without restart semantics."""
        try:
            task = asyncio.create_task(coro, name=name)
        except TypeError as exc:
            if "name" not in str(exc):
                close = getattr(coro, "close", None)
                if callable(close):
                    close()
                raise
            task = asyncio.create_task(coro)
        except BaseException:
            close = getattr(coro, "close", None)
            if callable(close):
                close()
            raise

        self._tasks[task] = {
            "group": group,
            "name": name,
            "kind": kind,
            "created_at": time.time(),
            "heartbeat_at": None,
            "restart_count": 0,
            "last_error": None,
            "state": "running",
            "restart_at": None,
        }
        add_done_callback = getattr(task, "add_done_callback", None)
        if callable(add_done_callback):
            add_done_callback(self._on_task_done)
        return task

    def create_resilient(
        self,
        group: str,
        factory: Callable[[], Coroutine[Any, Any, Any]],
        *,
        name: str,
        max_restarts: int | None = None,
        service: bool = True,
    ) -> asyncio.Task[Any]:
        """Create a service task that restarts after unexpected exit/failure."""

        task_ref: dict[str, asyncio.Task[Any]] = {}

        async def runner() -> None:
            delay = 1.0
            while True:
                task = task_ref.get("task")
                meta = self._tasks.get(task) if task is not None else None
                if meta is not None:
                    meta["state"] = "running"
                    meta["restart_at"] = None
                    meta["last_error"] = None
                try:
                    await factory()
                    if not service:
                        return
                    error_text = "worker exited unexpectedly"
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001 - service boundary
                    error_text = f"{type(exc).__name__}: {exc}"
                    log.exception("Background service %s failed", name)

                if meta is not None:
                    meta["last_error"] = error_text
                    meta["restart_count"] += 1
                    restart_count = int(meta["restart_count"])
                else:
                    restart_count = 1

                if max_restarts is not None and restart_count > max_restarts:
                    raise RuntimeError(
                        f"background service {name} exceeded restart limit: {error_text}"
                    )

                if meta is not None:
                    meta["state"] = "restarting"
                    meta["restart_at"] = time.time() + delay

                log.warning(
                    "Restarting background service %s in %.0fs after %s",
                    name,
                    delay,
                    error_text,
                )
                await asyncio.sleep(delay)
                delay = min(delay * 2.0, 60.0)

        task = self.create(group, runner(), name=name, kind="service")
        task_ref["task"] = task
        return task

    def _on_task_done(self, task: asyncio.Task[Any]) -> None:
        meta = self._tasks.get(task)
        if meta is None or task.cancelled():
            return
        try:
            exc = task.exception()
        except (asyncio.CancelledError, AttributeError):
            return
        if exc is not None:
            meta["last_error"] = f"{type(exc).__name__}: {exc}"
            log.error(
                "Supervised task %s stopped: %s",
                self._task_name(task, str(meta["name"])),
                meta["last_error"],
            )

    def heartbeat(self, group: str, name: str) -> bool:
        """Record progress for a running service task."""
        for task, meta in tuple(self._tasks.items()):
            if task.done():
                continue
            if meta["group"] == group and meta["name"] == name:
                meta["heartbeat_at"] = time.time()
                return True
        return False

    def snapshot(self, *, include_done: bool = True) -> list[TaskInfo]:
        """Return current task state for status/diagnostics."""
        result: list[TaskInfo] = []
        for task, meta in tuple(self._tasks.items()):
            if task.cancelled():
                status = "cancelled"
            elif task.done():
                status = "failed" if meta.get("last_error") else "done"
            else:
                status = str(meta.get("state") or "running")
            if not include_done and status in {"done", "cancelled"}:
                continue
            result.append(
                TaskInfo(
                    group=str(meta["group"]),
                    name=str(meta["name"]),
                    status=status,
                    kind=str(meta["kind"]),
                    created_at=float(meta["created_at"]),
                    heartbeat_at=meta.get("heartbeat_at"),
                    restart_count=int(meta.get("restart_count") or 0),
                    last_error=meta.get("last_error"),
                    restart_at=meta.get("restart_at"),
                )
            )
        return sorted(result, key=lambda item: (item.group, item.name))

    def stale_services(self, max_age_seconds: float) -> list[TaskInfo]:
        """Return services whose explicit progress heartbeat is too old."""
        now = time.time()
        return [
            info
            for info in self.snapshot(include_done=False)
            if info.kind == "service"
            and info.status == "running"
            and info.heartbeat_at is not None
            and now - info.heartbeat_at > max_age_seconds
        ]

    def owns(self, task: object | None) -> bool:
        """Return whether *task* is owned by this supervisor."""
        if task is None:
            return False
        try:
            return task in self._tasks
        except TypeError:
            return False

    async def cancel_group(self, group: str, *, timeout: float = 5.0) -> int:
        """Cancel all running tasks in one lifecycle group."""
        tasks = [
            task
            for task, meta in tuple(self._tasks.items())
            if meta["group"] == group and not task.done()
        ]
        for task in tasks:
            task.cancel()
        if tasks:
            done, pending = await asyncio.wait(tasks, timeout=timeout)
            for task in done:
                try:
                    task.result()
                except asyncio.CancelledError:
                    # Cancellation is the expected result while shutting down this task group.
                    continue
                except Exception as exc:  # noqa: BLE001 - cleanup boundary
                    log.debug("Task raised while cancelling", exc_info=exc)
            for task in pending:
                log.warning(
                    "Background task did not stop within %.1fs: %s",
                    timeout,
                    self._task_name(task, "unknown"),
                )

        for task, meta in tuple(self._tasks.items()):
            if meta["group"] == group and task.done() and meta.get("last_error") is None:
                self._tasks.pop(task, None)
        return len(tasks)

    async def cancel_all(self, *, timeout: float = 5.0) -> int:
        """Cancel all running supervised tasks."""
        groups = {str(meta["group"]) for meta in self._tasks.values()}
        cancelled = 0
        for group in groups:
            cancelled += await self.cancel_group(group, timeout=timeout)
        return cancelled
