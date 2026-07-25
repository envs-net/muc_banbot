"""Periodic health checks for protected rooms and maintenance cleanup."""

import asyncio
import logging
import time

from config import ADMIN_ROOM, NICK
from .locks import is_maintenance_mode

log = logging.getLogger(__name__)


class HealthCheckMixin:
    async def _health_send_alert(self, key: str, title: str, message: str, *, enabled: bool = True, details: dict | None = None) -> bool:
        """Send an operational alert when AlertMixin is available, otherwise fall back to ADMIN_ROOM."""
        if not enabled:
            return False
        if hasattr(self, "send_operational_alert"):
            return await self.send_operational_alert(
                key,
                title,
                message,
                enabled=enabled,
                details=details,
            )
        await self.bot_send_message(mto=ADMIN_ROOM, mbody=f"⚠️ {title}\n{message}", mtype="groupchat")
        return True

    async def _health_record_failure(self, key: str, title: str, message: str, *, enabled: bool = True, threshold: int = 1, details: dict | None = None) -> bool:
        """Record an alert failure when AlertMixin is available, otherwise alert immediately."""
        if hasattr(self, "record_alert_failure"):
            return await self.record_alert_failure(
                key,
                title,
                message,
                enabled=enabled,
                threshold=threshold,
                details=details,
            )
        return await self._health_send_alert(key, title, message, enabled=enabled, details=details)

    def _health_record_success(self, key: str) -> None:
        if hasattr(self, "record_alert_success"):
            self.record_alert_success(key)

    async def health_check_worker(self) -> None:
        """
        Periodically check connection status of all protected rooms.
        Verifies bot is still in rooms and has admin rights.
        Uses self.health_check_interval (reloadable via !reloadconfig).
        """
        while True:
            try:
                await asyncio.sleep(self.health_check_interval)

                if is_maintenance_mode(self):
                    log.debug("Health check skipped while maintenance operation is active")
                    continue

                if getattr(self, "reconnecting", False):
                    log.debug("Health check skipped while reconnecting")
                    continue

                reconnect_task = getattr(self, "reconnect_task", None)
                if reconnect_task and not reconnect_task.done():
                    log.debug("Health check skipped while reconnect task is active")
                    continue

                # Keep audit retention active during long-running bot sessions,
                # but do not run the cleanup more than once per day.
                if time.time() - self.last_audit_cleanup_run >= 86400:
                    await self.cleanup_old_audit_logs()

                try:
                    db_stats = await self.get_db_stats()
                    self._health_record_success("db_stats")
                    db_size_mb_limit = int(getattr(self, "alert_on_db_size_mb", 0) or 0)
                    db_size_bytes = int(db_stats.get("db_size_bytes", 0) or 0)
                    if db_size_mb_limit > 0 and db_size_bytes >= db_size_mb_limit * 1024 * 1024:
                        await self._health_send_alert(
                            "db_size_limit",
                            "Database size alert",
                            f"Database size is {db_size_bytes / 1024 / 1024:.1f} MiB (limit: {db_size_mb_limit} MiB).",
                            enabled=True,
                            details={"db_size_bytes": db_size_bytes, "limit_mb": db_size_mb_limit},
                        )
                    else:
                        self._health_record_success("db_size_limit")
                except Exception as e:
                    log.warning("Health check database stats failed: %s", e)
                    await self._health_record_failure(
                        "db_stats",
                        "Database stats failed",
                        f"Could not read database stats during health check: {e}",
                        enabled=getattr(self, "alert_on_db_stats_failure", True),
                        threshold=1,
                        details={"error": str(e)},
                    )

                for room in self.protected_rooms | {ADMIN_ROOM}:
                    try:
                        # Check if bot is still in room
                        occ = self.occupants.get(room, {})
                        bot_entry = getattr(self, "_bot_occupant_entry", None)
                        if bot_entry is not None:
                            bot_in_room = bot_entry(room)[1] is not None
                        else:
                            bot_in_room = any(nick.lower() == NICK.lower() for nick in occ.keys())
                        if not bot_in_room:
                            log.warning("⚠️ Health check: Bot not found in occupants for room %s; attempting rejoin", room)
                            rejoined = False
                            ensure_joined = getattr(self, "ensure_muc_joined", None)
                            if ensure_joined is not None:
                                rejoined = await ensure_joined(
                                    room,
                                    timeout=20,
                                    retries=2,
                                    force=True,
                                )

                            if not rejoined:
                                await self._health_send_alert(
                                    f"health_not_in_room:{room}",
                                    "Health check warning",
                                    f"Bot not in room {room}; automatic rejoin failed",
                                    enabled=getattr(self, "alert_on_health_check_failure", True),
                                    details={"room": room, "reason": "automatic_rejoin_failed"},
                                )
                                continue

                            log.info("✅ Health check rejoined %s", room)
                            self._health_record_success(f"health_not_in_room:{room}")

                        # Check admin rights
                        if not self.is_bot_admin_or_owner(room):
                            log.warning("⚠️ Health check: Bot lost admin rights in %s", room)
                            await self._health_send_alert(
                                f"admin_rights_lost:{room}",
                                "Bot lost admin/owner rights",
                                f"Bot lost admin/owner rights in {room}",
                                enabled=getattr(self, "alert_on_admin_rights_lost", True),
                                details={"room": room},
                            )

                    except Exception as e:
                        log.warning("Health check error for %s: %s", room, e)
                        await self._health_record_failure(
                            f"health_check_error:{room}",
                            "Health check error",
                            f"Health check failed for {room}: {e}",
                            enabled=getattr(self, "alert_on_health_check_failure", True),
                            threshold=1,
                            details={"room": room, "error": str(e)},
                        )
                    else:
                        self._health_record_success(f"health_check_error:{room}")

            except asyncio.CancelledError:
                log.info("health_check_worker cancelled")
                raise

            except Exception as e:
                log.warning("Error in health_check_worker: %s", e)
                await asyncio.sleep(10)
