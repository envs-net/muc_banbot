"""Periodic health checks for protected rooms and maintenance cleanup."""

import asyncio
import logging
import time

from config import ADMIN_ROOM, NICK

log = logging.getLogger(__name__)


class HealthCheckMixin:
    async def health_check_worker(self) -> None:
        """
        Periodically check connection status of all protected rooms.
        Verifies bot is still in rooms and has admin rights.
        Uses self.health_check_interval (reloadable via !reloadconfig).
        """
        while True:
            try:
                await asyncio.sleep(self.health_check_interval)

                # Keep audit retention active during long-running bot sessions,
                # but do not run the cleanup more than once per day.
                if time.time() - self.last_audit_cleanup_run >= 86400:
                    await self.cleanup_old_audit_logs()

                for room in self.protected_rooms:
                    try:
                        # Check if bot is still in room
                        occ = self.occupants.get(room, {})
                        bot_in_room = any(nick.lower() == NICK.lower() for nick in occ.keys())
                        if not bot_in_room:
                            log.warning("⚠️ Health check: Bot not found in occupants for room %s", room)
                            await self.bot_send_message(
                                mto=ADMIN_ROOM,
                                mbody=f"⚠️ Health check warning: Bot not in room {room} occupants",
                                mtype="groupchat"
                            )
                            continue

                        # Check admin rights
                        if not self.is_bot_admin_or_owner(room):
                            log.warning("⚠️ Health check: Bot lost admin rights in %s", room)
                            await self.bot_send_message(
                                mto=ADMIN_ROOM,
                                mbody=f"⚠️ Health check: Bot lost admin/owner rights in {room}",
                                mtype="groupchat"
                            )

                    except Exception as e:
                        log.warning("Health check error for %s: %s", room, e)

            except asyncio.CancelledError:
                log.info("health_check_worker cancelled")
                raise

            except Exception as e:
                log.warning("Error in health_check_worker: %s", e)
                await asyncio.sleep(10)
