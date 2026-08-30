"""systemd watchdog integration and event-loop lag diagnostics."""

from __future__ import annotations

import asyncio
import logging
import os
import socket
import time
from dataclasses import asdict, dataclass
from typing import Any

log = logging.getLogger(__name__)


def sd_notify(payload: str) -> bool:
    """Send an sd_notify datagram without depending on python-systemd."""
    address = os.environ.get("NOTIFY_SOCKET", "")
    if not address:
        return False
    if address.startswith("@"):
        address = "\0" + address[1:]
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    try:
        sock.connect(address)
        sock.sendall(payload.encode("utf-8"))
        return True
    except OSError:
        log.debug("systemd sd_notify failed", exc_info=True)
        return False
    finally:
        sock.close()


def systemd_watchdog_interval(default: float) -> float:
    """Return a heartbeat cadence safely below systemd's watchdog timeout."""
    try:
        watchdog_usec = int(os.environ.get("WATCHDOG_USEC", "0") or 0)
    except (TypeError, ValueError):
        watchdog_usec = 0
    if watchdog_usec <= 0:
        return max(1.0, float(default))
    return max(1.0, min(float(default), watchdog_usec / 2_000_000.0))


@dataclass
class WatchdogState:
    enabled: bool = False
    systemd_active: bool = False
    worker_running: bool = False
    heartbeats: int = 0
    last_heartbeat_at: int = 0
    last_lag_seconds: float = 0.0
    max_lag_seconds: float = 0.0
    lag_warnings: int = 0
    heartbeat_suppressed: int = 0
    last_error: str | None = None


class RuntimeWatchdog:
    """Monitor event-loop responsiveness and feed systemd's watchdog."""

    def __init__(self, bot: Any):
        self.bot = bot
        self.task: asyncio.Task[Any] | None = None
        self.stop_event = asyncio.Event()
        self.state = WatchdogState()

    async def start(self) -> None:
        configured = bool(getattr(self.bot, "watchdog_enabled", True))
        self.state.systemd_active = bool(
            os.environ.get("NOTIFY_SOCKET") and os.environ.get("WATCHDOG_USEC")
        )
        # If systemd configured WatchdogSec, heartbeats are mandatory even when
        # the application setting is disabled. Disable WatchdogSec as well when
        # intentionally turning monitoring off.
        self.state.enabled = configured or self.state.systemd_active
        if not self.state.enabled:
            return
        if self.task is not None and not self.task.done():
            return

        self.stop_event = asyncio.Event()
        supervisor = getattr(self.bot, "tasks", None)
        if supervisor is not None:
            self.task = supervisor.create_resilient(
                "_runtime",
                self._run,
                name="runtime-watchdog",
                service=True,
            )
        else:
            self.task = asyncio.create_task(self._run(), name="runtime-watchdog")
        self.state.worker_running = True

    def notify_ready(self) -> bool:
        """Tell systemd that complete BanBot startup has succeeded."""
        status = (
            "muc_banbot started and monitoring event-loop health"
            if self.state.enabled
            else "muc_banbot startup complete"
        )
        return sd_notify(f"READY=1\nSTATUS={status}")

    async def stop(self) -> None:
        self.stop_event.set()
        task = self.task
        self.task = None
        if task is not None and not task.done():
            supervisor = getattr(self.bot, "tasks", None)
            owns = getattr(supervisor, "owns", None) if supervisor is not None else None
            if supervisor is not None and callable(owns) and owns(task):
                await supervisor.cancel_group("_runtime", timeout=5.0)
            else:
                task.cancel()
                if isinstance(task, asyncio.Future):
                    done, pending = await asyncio.wait({task}, timeout=5.0)
                    for finished in done:
                        try:
                            finished.result()
                        except asyncio.CancelledError:
                            continue
                        except Exception as exc:  # noqa: BLE001 - cleanup boundary
                            log.debug("Runtime watchdog raised while stopping", exc_info=exc)
                    if pending:
                        log.warning("Runtime watchdog did not stop within 5.0s")
                else:
                    # Compatibility for lightweight embedders/tests. Production
                    # watchdog workers are asyncio Tasks and use the bounded path.
                    try:
                        await task
                    except asyncio.CancelledError:
                        log.debug("Runtime watchdog cancelled during shutdown")
        self.state.worker_running = False
        sd_notify("STOPPING=1\nSTATUS=muc_banbot shutting down")

    async def _run(self) -> None:
        # The supervisor may invoke this coroutine again after a failure. Mark
        # each new invocation as running so status recovers after a restart.
        self.state.worker_running = True
        configured_interval = max(
            1.0,
            float(getattr(self.bot, "watchdog_interval_seconds", 20) or 20),
        )
        interval = systemd_watchdog_interval(configured_interval)
        warning_threshold = max(
            0.1,
            float(getattr(self.bot, "watchdog_lag_warning_seconds", 2.0) or 2.0),
        )
        failure_threshold = max(
            warning_threshold,
            float(getattr(self.bot, "watchdog_lag_failure_seconds", 30.0) or 30.0),
        )
        loop = asyncio.get_running_loop()
        expected = loop.time() + interval
        try:
            while not self.stop_event.is_set():
                try:
                    await asyncio.wait_for(self.stop_event.wait(), timeout=interval)
                    break
                except TimeoutError:
                    # The timeout is the normal heartbeat interval; continue with the lag check.
                    pass

                now = loop.time()
                lag = max(0.0, now - expected)
                expected = now + interval
                self.state.last_lag_seconds = lag
                self.state.max_lag_seconds = max(self.state.max_lag_seconds, lag)
                if lag >= warning_threshold:
                    self.state.lag_warnings += 1
                    log.warning(
                        "Event-loop lag %.3fs exceeds %.3fs",
                        lag,
                        warning_threshold,
                    )

                supervisor = getattr(self.bot, "tasks", None)
                if supervisor is not None:
                    supervisor.heartbeat("_runtime", "runtime-watchdog")

                if lag >= failure_threshold:
                    self.state.heartbeat_suppressed += 1
                    sd_notify(
                        "STATUS=muc_banbot unhealthy: "
                        f"event-loop lag {lag:.3f}s; watchdog heartbeat suppressed"
                    )
                    continue

                if sd_notify(
                    "WATCHDOG=1\n"
                    f"STATUS=muc_banbot healthy; event-loop lag {lag:.3f}s"
                ):
                    self.state.heartbeats += 1
                    self.state.last_heartbeat_at = int(time.time())
                self.state.last_error = None
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.state.last_error = f"{type(exc).__name__}: {exc}"
            log.exception("Runtime watchdog failed")
            raise
        finally:
            self.state.worker_running = False

    def runtime_state(self) -> dict[str, Any]:
        return asdict(self.state)
