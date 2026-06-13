"""Notification helpers for protection-related policy changes."""

from __future__ import annotations

from typing import Any

from config import ADMIN_ROOM

from ..utils import safe_jid


class ProtectionNotificationMixin:
    async def notify_policy_change(
        self,
        event: str,
        *,
        actor: str | None = None,
        target: str | None = None,
        room: str | None = None,
        comment: str | None = None,
    ) -> None:
        """Send admin-room notification for policy changes when enabled."""
        protection = "PolicyChangeNotification"
        if not self.protection_enabled(protection):
            return
        config = self.protection_config(protection)
        if event.startswith("ban") and not bool(config.get("notify_bans", True)):
            return
        if event.startswith("unban") and not bool(config.get("notify_unbans", True)):
            return
        await self.bot_send_message(
            mto=ADMIN_ROOM,
            mbody=(
                "📣 Policy change\n"
                f"Event: {event}\n"
                f"Target: {safe_jid(target or 'unknown')}\n"
                f"Actor: {safe_jid(actor or 'unknown')}"
                + (f"\nRoom: {room}" if room else "")
                + (f"\nComment: {comment}" if comment else "")
            ),
            mtype="groupchat",
        )
