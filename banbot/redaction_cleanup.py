"""Redaction index retention and cleanup workers."""

from __future__ import annotations

import asyncio
import logging
import time

from envs_xmpp_core.runtime.diagnostics import exception_summary

from .redaction_common import REDACTION_CLEANUP_INTERVAL_SECONDS, _RedactionMixinContract
from .task_supervisor import sleep_with_heartbeat

log = logging.getLogger(__name__)


class RedactionCleanupMixin(_RedactionMixinContract):
    async def _redaction_cleanup_old_entries(
        self,
        actor: str | None = "system",
        *,
        audit: bool = True,
        audit_noop: bool = True,
    ) -> dict[str, int | bool | str]:
        """Delete expired redaction index rows without sending command output.

        ``audit_noop`` controls whether no-op cleanup runs are written to the
        audit log. Manual cleanup keeps no-op audit entries, while automatic
        cleanup only records runs that actually delete rows. This avoids noisy
        recurring audit events when retention is disabled or nothing expired.
        """
        result: dict[str, int | bool | str] = {
            "enabled": bool(getattr(self, "redaction_enabled", False)),
            "retention_days": int(getattr(self, "redaction_index_retention_days", 30) or 0),
            "deleted": 0,
        }

        if not result["enabled"]:
            result["skipped_reason"] = "redaction disabled"
            return result

        days = int(result["retention_days"])
        if days <= 0:
            result["skipped_reason"] = "retention disabled"
            if audit and audit_noop:
                await self._audit_redaction_event(
                    "redact_cleanup",
                    actor=actor,
                    target_type="redaction_index",
                    target="cleanup",
                    comment="retention disabled; keep forever",
                    details={"retention_days": days, "deleted": 0},
                )
            return result

        db = self._require_db()
        cutoff = int(time.time()) - days * 86400
        cur = await db.execute(
            "DELETE FROM redaction_index WHERE created_at < ?",
            (cutoff,),
        )
        deleted = cur.rowcount or 0
        result["deleted"] = deleted
        await db.commit()

        if audit and (deleted > 0 or audit_noop):
            await self._audit_redaction_event(
                "redact_cleanup",
                actor=actor,
                target_type="redaction_index",
                target="cleanup",
                comment=f"retention {days} days",
                details={"retention_days": days, "deleted": deleted},
            )

        return result


    async def run_redaction_cleanup_automatic(self, actor: str | None = "system") -> dict[str, int | bool | str]:
        """Run automatic redaction index cleanup without admin-room noise."""
        try:
            result = await self._redaction_cleanup_old_entries(actor=actor, audit=True, audit_noop=False)
        except Exception as exc:
            log.warning("Automatic redaction index cleanup failed: %s", exception_summary(exc))
            if hasattr(self, "send_operational_alert"):
                await self.send_operational_alert(
                    "redaction_cleanup_failed",
                    "Redaction cleanup failed",
                    f"Automatic redaction index cleanup failed: {exception_summary(exc)}",
                    enabled=getattr(self, "alert_on_redaction_failure", True),
                    details={"error": exception_summary(exc)},
                )
            return {"enabled": bool(getattr(self, "redaction_enabled", False)), "deleted": 0, "error": exception_summary(exc)}

        deleted = int(result.get("deleted", 0) or 0)
        if deleted > 0:
            log.info("Redaction index cleanup removed %s old entr%s", deleted, "y" if deleted == 1 else "ies")
        return result


    async def redaction_cleanup_worker(self) -> None:
        """Run redaction index cleanup every 24 hours."""
        while True:
            await sleep_with_heartbeat(
                self,
                "redaction-cleanup-worker",
                REDACTION_CLEANUP_INTERVAL_SECONDS,
                sleep_func=asyncio.sleep,
            )
            await self.run_redaction_cleanup_automatic(actor="system")


    async def redact_cleanup(self, room: str, actor: str | None = None) -> None:
        """Delete old redaction index rows according to configured retention."""
        if not getattr(self, "redaction_enabled", False):
            await self.bot_send_message(
                mto=room,
                mbody=(
                    "❌ Redaction is disabled.\n"
                    f"Set REDACTION_ENABLED=True and run {getattr(self, 'command_prefix', '!')}reloadconfig to use it."
                ),
                mtype="groupchat",
            )
            return

        result = await self._redaction_cleanup_old_entries(actor=actor, audit=True)
        days = int(result.get("retention_days", 0) or 0)
        deleted = int(result.get("deleted", 0) or 0)

        if days <= 0:
            await self.bot_send_message(
                mto=room,
                mbody=(
                    "🧹 Redaction index cleanup completed\n\n"
                    "Retention: keep forever\n"
                    "Deleted entries: 0\n"
                    "Note: cleanup is disabled when retention is set to keep forever."
                ),
                mtype="groupchat",
            )
            return

        await self.bot_send_message(
            mto=room,
            mbody=(
                "🧹 Redaction index cleanup completed\n\n"
                f"Retention: {days} days\n"
                f"Deleted entries: {deleted}\n"
                "Note: cleanup only removes entries older than the retention period."
            ),
            mtype="groupchat",
        )
