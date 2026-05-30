"""Operational alert helpers for BanBot."""

from __future__ import annotations

import logging
import time
from typing import Any

from config import ADMIN_ROOM

log = logging.getLogger(__name__)


class AlertMixin:
    """Small deduplicated ADMIN_ROOM alert layer."""

    def init_alert_state(self) -> None:
        self.alert_last_sent: dict[str, float] = {}
        self.alert_counters: dict[str, int] = {}

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

        now = time.time()
        dedup_window = max(0, int(getattr(self, "alert_dedup_window", 300) or 0))
        last_sent = self.alert_last_sent.get(key, 0.0)
        if dedup_window and now - last_sent < dedup_window:
            log.debug("Alert %s suppressed by %ss dedup window", key, dedup_window)
            return False

        self.alert_last_sent[key] = now
        body = f"⚠️ {title}\n{message}"
        try:
            await self.bot_send_message(mto=ADMIN_ROOM, mbody=body, mtype="groupchat")
        except Exception as exc:
            log.warning("Failed to send alert %s: %s", key, exc)
            return False

        try:
            await self.audit_event(
                "operational_alert",
                actor="system",
                room=ADMIN_ROOM,
                details={"key": key, "title": title, **(details or {})},
            )
        except Exception as exc:
            log.debug("Failed to audit alert %s: %s", key, exc)

        return True

    def record_alert_success(self, key: str) -> None:
        """Reset consecutive failure counters after a successful check."""
        self.alert_counters.pop(key, None)

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
        count = self.alert_counters.get(key, 0) + 1
        self.alert_counters[key] = count
        if count < threshold:
            return False
        return await self.send_operational_alert(
            key,
            title,
            f"{message}\nConsecutive failures: {count}",
            enabled=enabled,
            details={"consecutive_failures": count, **(details or {})},
        )
