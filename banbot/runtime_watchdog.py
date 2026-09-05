"""BanBot compatibility facade for the shared runtime watchdog."""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

from envs_xmpp_core.runtime.systemd import sd_notify as _core_sd_notify
from envs_xmpp_core.runtime.systemd import systemd_watchdog_interval
from envs_xmpp_core.runtime.watchdog import (
    RuntimeWatchdog as CoreRuntimeWatchdog,
    WatchdogOptions,
    WatchdogState,
)

log = logging.getLogger(__name__)

__all__ = ["RuntimeWatchdog", "WatchdogState", "sd_notify", "systemd_watchdog_interval"]

_STARTUP_TIMEOUT_EXTENSION_USEC = 300_000_000
_STARTUP_TIMEOUT_EXTENSION_INTERVAL_SECONDS = 60.0


def sd_notify(payload: str) -> bool:
    """Send an sd_notify datagram through the shared implementation."""
    return _core_sd_notify(payload)


def _notify(payload: str) -> bool:
    """Resolve the public notifier lazily so monkeypatching remains supported."""
    return sd_notify(payload)


class RuntimeWatchdog(CoreRuntimeWatchdog):
    """Preserve BanBot's runtime contract over the neutral core watchdog."""

    def __init__(self, bot: Any):
        self.bot = bot
        self.startup_timeout_task: asyncio.Task[Any] | None = None
        super().__init__(
            service_name="muc_banbot",
            options=self._options_from_bot(),
            supervisor=getattr(bot, "tasks", None),
            ready_predicate=self._runtime_ready_for_systemd,
            notifier=_notify,
            on_ready=self._cancel_startup_timeout_extension,
            options_provider=self._options_from_bot,
        )

    def _options_from_bot(self) -> WatchdogOptions:
        """Read settings lazily because BanBot applies runtime config later."""
        return WatchdogOptions(
            enabled=bool(getattr(self.bot, "watchdog_enabled", True)),
            interval_seconds=float(
                getattr(self.bot, "watchdog_interval_seconds", 20) or 20
            ),
            lag_warning_seconds=float(
                getattr(self.bot, "watchdog_lag_warning_seconds", 2.0) or 2.0
            ),
            lag_failure_seconds=float(
                getattr(self.bot, "watchdog_lag_failure_seconds", 30.0) or 30.0
            ),
            defer_ready_notification=True,
        )

    def arm_startup_timeout_extension(
        self,
        loop: asyncio.AbstractEventLoop | None = None,
    ) -> bool:
        """Extend Type=notify startup while waiting for the first XMPP session."""
        if self.ready_sent or not os.environ.get("NOTIFY_SOCKET"):
            return False

        task = self.startup_timeout_task
        if task is not None and not task.done():
            return True

        sent = sd_notify(
            "EXTEND_TIMEOUT_USEC="
            f"{_STARTUP_TIMEOUT_EXTENSION_USEC}\n"
            "STATUS=muc_banbot waiting for XMPP session"
        )

        if loop is None:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = getattr(self.bot, "loop", None)

        if loop is None or loop.is_closed():
            return sent

        self.startup_timeout_task = loop.create_task(
            self._run_startup_timeout_extension(),
            name="systemd-startup-timeout-extension",
        )
        return True

    def _cancel_startup_timeout_extension(self) -> asyncio.Task[Any] | None:
        task = self.startup_timeout_task
        self.startup_timeout_task = None
        if task is not None and not task.done():
            task.cancel()
        return task

    async def _run_startup_timeout_extension(self) -> None:
        current = asyncio.current_task()
        try:
            while (
                not self.ready_sent
                and not bool(getattr(self.bot, "_session_start_received", False))
            ):
                await asyncio.sleep(_STARTUP_TIMEOUT_EXTENSION_INTERVAL_SECONDS)
                if (
                    self.ready_sent
                    or bool(getattr(self.bot, "_session_start_received", False))
                ):
                    return
                sd_notify(
                    "EXTEND_TIMEOUT_USEC="
                    f"{_STARTUP_TIMEOUT_EXTENSION_USEC}\n"
                    "STATUS=muc_banbot waiting for XMPP session"
                )
        except asyncio.CancelledError:
            raise
        finally:
            if self.startup_timeout_task is current:
                self.startup_timeout_task = None

    def _runtime_ready_for_systemd(self) -> bool:
        """Return whether BanBot has completed its full runtime startup."""
        if not hasattr(self.bot, "health_check_task"):
            return True
        return (
            getattr(self.bot, "health_check_task", None) is not None
            and not bool(getattr(self.bot, "reconnecting", False))
        )

    async def stop(self) -> None:
        startup_task = self._cancel_startup_timeout_extension()
        if startup_task is not None and isinstance(startup_task, asyncio.Future):
            try:
                await startup_task
            except asyncio.CancelledError:
                log.debug("Startup timeout extender cancelled during shutdown")
            except Exception as exc:  # noqa: BLE001 - cleanup boundary
                log.debug(
                    "Startup timeout extender raised while stopping",
                    exc_info=exc,
                )

        await super().stop()
