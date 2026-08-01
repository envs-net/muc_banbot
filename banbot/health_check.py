"""Periodic health checks for protected rooms and maintenance cleanup."""

import asyncio
import logging
import time

from config import ADMIN_ROOM, NICK
from .locks import is_maintenance_mode

log = logging.getLogger(__name__)


class HealthCheckMixin:
    def _health_bot_occupant_entry(self, room: str) -> tuple[str | None, dict | None]:
        """Return the bot's live room entry for full and lightweight mixin users."""
        bot_entry = getattr(self, "_bot_occupant_entry", None)
        if bot_entry is not None:
            return bot_entry(room)

        for nick, info in self.occupants.get(room, {}).items():
            if str(nick).lower() == str(NICK).lower():
                return nick, info
        return None, None

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

    async def _health_check_room(self, room: str) -> None:
        """Check one room and perform a controlled automatic rejoin if needed."""
        _bot_nick, bot_info = self._health_bot_occupant_entry(room)
        bot_in_room = bot_info is not None

        if not bot_in_room:
            log.warning("⚠️ Health check: Bot not found in occupants for room %s; attempting rejoin", room)
            rejoined = False
            ensure_joined = getattr(self, "ensure_muc_joined", None)
            if ensure_joined is not None:
                rejoined = await ensure_joined(room, force=True)

            if not rejoined:
                await self._health_send_alert(
                    f"health_not_in_room:{room}",
                    "Health check warning",
                    f"Bot not in room {room}; automatic rejoin failed",
                    enabled=getattr(self, "alert_on_health_check_failure", True),
                    details={"room": room, "reason": "automatic_rejoin_failed"},
                )
                return

            _bot_nick, bot_info = self._health_bot_occupant_entry(room)
            is_admin = bool(bot_info and bot_info.get("affiliation") in ("owner", "admin"))
            admin_state = getattr(self, "bot_admin_state", None)
            if admin_state is None:
                admin_state = {}
                self.bot_admin_state = admin_state
            admin_state[room] = is_admin

            bans_synced = False
            sync_room_bans = getattr(self, "sync_bans_to_rooms_for_single_room", None)
            if is_admin and sync_room_bans is not None:
                await sync_room_bans(room)
                bans_synced = True

            log.info(
                "✅ Health check rejoined %s%s",
                room,
                " and resynced active bans" if bans_synced else "",
            )
            self._health_record_success(f"health_not_in_room:{room}")
            try:
                await self.bot_send_message(
                    mto=ADMIN_ROOM,
                    mbody=(
                        f"✅ Automatic room rejoin succeeded for {room}."
                        + (" Active bans were resynced." if bans_synced else "")
                    ),
                    mtype="groupchat",
                )
            except Exception as exc:
                # Recovery itself succeeded; a notification transport failure
                # must not turn the room health result back into a failure.
                log.warning("Could not announce automatic rejoin for %s: %s", room, exc)

        if not self.is_bot_admin_or_owner(room):
            log.warning("⚠️ Health check: Bot lost admin rights in %s", room)
            await self._health_send_alert(
                f"admin_rights_lost:{room}",
                "Bot lost admin/owner rights",
                f"Bot lost admin/owner rights in {room}",
                enabled=getattr(self, "alert_on_admin_rights_lost", True),
                details={"room": room},
            )

    async def _run_health_check_cycle(self) -> None:
        """Run one complete, independently testable health-check cycle."""
        if is_maintenance_mode(self):
            log.debug("Health check skipped while maintenance operation is active")
            return

        if getattr(self, "reconnecting", False):
            log.debug("Health check skipped while reconnecting")
            return

        reconnect_task = getattr(self, "reconnect_task", None)
        if reconnect_task and not reconnect_task.done():
            log.debug("Health check skipped while reconnect task is active")
            return

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
                await self._health_check_room(room)
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

    async def health_check_worker(self) -> None:
        """
        Periodically check connection status of all protected rooms.
        Verifies bot is still in rooms and has admin rights.
        Uses self.health_check_interval (reloadable via !reloadconfig).
        """
        while True:
            try:
                # Run once immediately after startup/reconnect so rooms whose
                # initial join timed out are retried without waiting for the
                # first full health-check interval. Later cycles keep using the
                # configured interval.
                await self._run_health_check_cycle()
                await asyncio.sleep(self.health_check_interval)

            except asyncio.CancelledError:
                log.info("health_check_worker cancelled")
                raise

            except Exception as e:
                log.warning("Error in health_check_worker: %s", e)
                await asyncio.sleep(10)
