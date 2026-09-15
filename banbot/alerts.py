"""Operational alert helpers for BanBot."""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any

from envs_xmpp_core.runtime.alerts import AlertTracker
from envs_xmpp_core.runtime.diagnostics import exception_summary

from config import ADMIN_ROOM

log = logging.getLogger(__name__)

if TYPE_CHECKING:
    from .contracts import AlertMixinHost

    class _AlertMixinContract(AlertMixinHost):
        pass
else:
    class _AlertMixinContract:
        pass


class AlertMixin(_AlertMixinContract):
    """Small deduplicated ADMIN_ROOM alert layer."""

    def init_alert_state(self) -> None:
        self.alert_tracker = AlertTracker()
        # Compatibility aliases remain useful to diagnostics/tests.
        self.alert_last_sent = self.alert_tracker.last_sent
        self.alert_counters = self.alert_tracker.counters

    async def send_operational_alert(
        self,
        key: str,
        title: str,
        message: str,
        *,
        enabled: bool = True,
        details: dict[str, Any] | None = None,
    ) -> bool:
        """Send a deduplicated alert to ADMIN_ROOM and audit it.

        Returns True when an alert was sent, False when disabled or deduplicated.
        """
        if not enabled:
            return False

        dedup_window = max(0, int(getattr(self, "alert_dedup_window", 300) or 0))
        if not self.alert_tracker.should_emit(
            key,
            now=time.time(),
            dedup_window_seconds=dedup_window,
        ):
            log.debug("Alert %s suppressed by %ss dedup window", key, dedup_window)
            return False

        body = f"⚠️ {title}\n{message}"
        try:
            accepted = await self.bot_send_message(
                mto=ADMIN_ROOM,
                mbody=body,
                mtype="groupchat",
                durable=True,
                category="operational_alert",
                dedupe_key=f"operational-alert:{key}",
            )
        except Exception as exc:
            self.alert_tracker.forget_emission(key)
            log.warning("Failed to queue alert %s: %s", key, exception_summary(exc))
            return False
        if accepted is False:
            self.alert_tracker.forget_emission(key)
            log.warning("Failed to queue alert %s", key)
            return False

        try:
            await self.audit_event(
                "operational_alert",
                actor="system",
                room=ADMIN_ROOM,
                details={"key": key, "title": title, **(details or {})},
            )
        except Exception as exc:
            log.debug("Failed to audit alert %s: %s", key, exception_summary(exc))

        return True

    def record_alert_success(self, key: str) -> None:
        """Reset consecutive failure counters after a successful check."""
        self.alert_tracker.record_success(key)

    async def record_alert_failure(
        self,
        key: str,
        title: str,
        message: str,
        *,
        enabled: bool = True,
        threshold: int = 1,
        details: dict[str, Any] | None = None,
    ) -> bool:
        """Increment a failure counter and alert when the threshold is reached."""
        threshold = max(1, int(threshold or 1))
        count = self.alert_tracker.record_failure(key)
        if count < threshold:
            return False
        return await self.send_operational_alert(
            key,
            title,
            f"{message}\nConsecutive failures: {count}",
            enabled=enabled,
            details={"consecutive_failures": count, **(details or {})},
        )
