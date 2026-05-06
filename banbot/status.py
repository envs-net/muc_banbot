"""Status and identity commands."""

import asyncio
import logging
import os
import time

import psutil
from config import ADMIN_ROOM

from ._version import __version__
from .utils import human_time

log = logging.getLogger(__name__)


class StatusMixin:
    async def _cmd_status(self, room: str) -> None:
        now = int(time.time())

        # Collect dynamic health signals for the headline.
        problems: list[str] = []
        warnings: list[str] = []
        notes: list[str] = []

        if getattr(self, "reconnecting", False):
            warnings.append("Reconnect/resync is currently in progress")

        last_reconnect_time = getattr(self, "last_reconnect_time", None)
        if last_reconnect_time:
            notes.append(f"Last reconnect completed {human_time(max(0, now - int(last_reconnect_time)))} ago")

        if not getattr(self, "db", None):
            problems.append("Database connection is not available")

        task_checks = [
            ("unban worker", getattr(self, "unban_task", None), True),
            ("health check worker", getattr(self, "health_check_task", None), True),
            (
                "version check worker",
                getattr(self, "version_check_task", None),
                bool(getattr(self, "version_check_enabled", False) and getattr(self, "version_check_url", None)),
            ),
            (
                "RTBL refresh worker",
                getattr(self, "_rtbl_refresh_task", None),
                bool(
                    getattr(self, "rtbl_enabled", False)
                    and getattr(self, "rtbl_refresh_interval", 0) > 0
                ),
            ),
        ]
        for task_name, task, should_run in task_checks:
            if should_run and task is not None and task.done():
                problems.append(f"{task_name} stopped unexpectedly")

        protected_rooms = sorted(getattr(self, "protected_rooms", set()))
        if not protected_rooms:
            warnings.append("No protected rooms configured")

        missing_admin_rooms = sorted(
            room_name
            for room_name in protected_rooms
            if getattr(self, "bot_admin_state", {}).get(room_name) is False
        )
        if missing_admin_rooms:
            preview = ", ".join(missing_admin_rooms[:5])
            if len(missing_admin_rooms) > 5:
                preview += f", … +{len(missing_admin_rooms) - 5} more"
            problems.append(f"Missing admin/owner rights in: {preview}")

        unconfirmed_admin_rooms = sorted(
            room_name
            for room_name in protected_rooms
            if room_name not in getattr(self, "bot_admin_state", {})
        )
        if unconfirmed_admin_rooms:
            warnings.append(f"Admin rights not confirmed yet in {len(unconfirmed_admin_rooms)} room(s)")

        admin_infos = self.occupants.get(ADMIN_ROOM, {})
        admins = sorted(set(
            self.safe_jid(info.get("jid", "unknown"))
            for info in admin_infos.values()
            if info.get("affiliation") in ("owner", "admin")
        ))
        if not admins:
            warnings.append("No admins/owners found in the admin room")

        if getattr(self, "admin_affiliation_query_forbidden_rooms", set()):
            warnings.append(
                f"Affiliation-query fallback active in "
                f"{len(self.admin_affiliation_query_forbidden_rooms)} room(s)"
            )

        if getattr(self, "rtbl_enabled", False) and not getattr(self, "rtbl_subscriptions", []):
            warnings.append("RTBL is enabled but no subscriptions are configured")

        # database stats
        try:
            db_stats = await self.get_db_stats()
        except Exception as e:
            db_stats = {}
            problems.append(f"Database stats failed: {e}")
            log.warning("Could not get database stats: %s", e)

        expired_ban_rows = int(db_stats.get("expired_ban_rows", 0) or 0)
        if expired_ban_rows > 0:
            warnings.append(f"{expired_ban_rows} expired tempban(s) are waiting for auto-unban")

        if problems:
            status_lines = ["❌ Bot is online, but problems were detected."]
        elif warnings:
            status_lines = ["⚠️ Bot is online, but attention is needed."]
        elif last_reconnect_time:
            status_lines = [
                "✅ Bot is online and healthy "
                f"(last reconnect: {human_time(max(0, now - int(last_reconnect_time)))} ago)."
            ]
        else:
            status_lines = ["✅ Bot is online and healthy."]

        if problems or warnings or notes:
            status_lines.append("")
            if problems:
                status_lines.append("❌ Problems:")
                status_lines.extend(f"  • {item}" for item in problems)
            if warnings:
                status_lines.append("⚠️ Warnings:")
                status_lines.extend(f"  • {item}" for item in warnings)
            if notes:
                status_lines.append("ℹ️ Notes:")
                status_lines.extend(f"  • {item}" for item in notes)

        # version
        status_lines.append(f"\n🤖 Bot Version: {__version__}")
        if self.last_version_check_result:
            status_lines.append(f"🏷️ Latest Release Version: {self.last_version_check_result}")

        # uptime
        bot_uptime = now - int(self.bot_start_time)
        status_lines.append(f"\n⏱️ Bot Uptime: {human_time(bot_uptime)}")

        if self.server_connect_time:
            server_uptime = now - int(self.server_connect_time)
            status_lines.append(f"🌐 Server Connected: {human_time(server_uptime)}")

        # mem info
        try:
            process = psutil.Process(os.getpid())
            memory_info = process.memory_info()
            memory_mb = memory_info.rss / 1024 / 1024
            status_lines.append(f"💾 Memory Usage: {memory_mb:.1f} MB")
        except Exception as e:
            log.debug("Could not get memory info: %s", e)

        # cpu info
        try:
            process = psutil.Process(os.getpid())
            loop = asyncio.get_running_loop()

            # psutil samples over 1 second; run in executor so the event loop stays responsive
            cpu_percent = await loop.run_in_executor(None, process.cpu_percent, 1.0)
            cpu_load = psutil.getloadavg()[0]
            cpu_count = psutil.cpu_count()

            status_lines.append(f"🧠 CPU Usage: {cpu_percent:.1f}% (Process)")
            status_lines.append(f"⚙️ System Load: {cpu_load:.2f} ({cpu_count} cores)")
        except Exception as e:
            log.debug("Could not get CPU info: %s", e)

        permanent_bans = db_stats.get("permanent_bans", 0)
        temporary_bans = db_stats.get("temporary_bans", 0)
        audit_events = db_stats.get("audit_events", 0)
        db_size_kib = int(db_stats.get("db_size_bytes", 0) or 0) / 1024

        status_lines.append(f"\n📊 Active Bans: {permanent_bans} permanent, {temporary_bans} temporary")
        status_lines.append(f"🧹 Expired tempbans pending auto-unban: {expired_ban_rows}")
        status_lines.append(f"🧾 Audit Events: {audit_events} (retention: {self.audit_log_retention_days}d)")
        status_lines.append(f"💽 DB Size: {db_size_kib:.1f} KiB")
        if self.admin_affiliation_query_forbidden_rooms:
            status_lines.append(f"⚠️ Affiliation-query fallback rooms: {len(self.admin_affiliation_query_forbidden_rooms)}")
        if self.last_import_backup_file:
            status_lines.append(f"💾 Last Import Backup: {self.last_import_backup_file}")

        # rtbl
        if getattr(self, "rtbl_enabled", False):
            rtbl_hashes = len(getattr(self, "rtbl_hash_cache", {}))
            rtbl_domains = len(getattr(self, "rtbl_domain_cache", {}))
            rtbl_subscriptions = len(getattr(self, "rtbl_subscriptions", []))
            status_lines.append(f"\n🛡️ RTBL Entries: {rtbl_hashes} JID hashes, {rtbl_domains} domains")
            status_lines.append(f"📋 RTBL Subscriptions: {rtbl_subscriptions}")

        if getattr(self, "rtbl_publish_enabled", False):
            status_lines.append(f"📡 RTBL Publish Enabled: {getattr(self, 'rtbl_publish_enabled', False)}")
            status_lines.append(f"   Service:     {self.rtbl_publish_service}")
            status_lines.append(f"   JID node:    {self.rtbl_publish_jid_node}")
            status_lines.append(f"   Domain node: {self.rtbl_publish_domain_node}")

        # admins
        status_lines.append(
            "\n🛡️ Admins/Owners in Admin-Room:\n" + "\n".join(admins)
            if admins else "\n⚠️ No admins/owners found in Admin-Room."
        )

        # protected rooms
        if protected_rooms:
            preview_count = 10
            preview_rooms = protected_rooms[:preview_count]

            status_lines.append(
                f"\n🔒 Protected Rooms ({len(protected_rooms)}):\n" +
                "\n".join(preview_rooms)
            )

            remaining = len(protected_rooms) - len(preview_rooms)
            if remaining > 0:
                status_lines.append(
                    f"\n... and {remaining} more.\n"
                    f"Use {self.command_prefix}room list [page] to view all protected rooms."
                )
        else:
            status_lines.append("\n⚠️ No protected rooms configured.")

        self.send_message(mto=room, mbody="\n".join(status_lines), mtype="groupchat")


    async def _cmd_whoami(self, room: str, nick: str) -> None:
        info = self.occupants.get(room, {}).get(nick, {})
        affiliation = info.get("affiliation", "none")
        role = info.get("role", "none")
        jid = info.get("jid", "unknown")

        permissions = []
        if affiliation in ("owner", "admin"):
            permissions.append("✅ Can ban/kick users")
            permissions.append("✅ Can manage room")
        elif role == "moderator":
            permissions.append("✅ Can kick users")
        else:
            permissions.append("❌ Regular participant")

        perms_text = "\n".join(permissions)

        if room == ADMIN_ROOM:
            message = (
                f"👤 **Your Status:**\n"
                f"  Nick: {nick}\n"
                f"  JID: {jid}\n"
                f"  Affiliation: {affiliation}\n"
                f"  Role: {role}\n\n"
                f"**Permissions:**\n{perms_text}"
            )
        else:
            emoji = "🔑" if affiliation in ("owner", "admin") else "👤"
            message = (
                f"{emoji} **Your Status:**\n"
                f"  Affiliation: {affiliation}\n"
                f"  Role: {role}\n\n"
                f"**Permissions:**\n{perms_text}"
            )

        self.send_message(mto=room, mbody=message, mtype="groupchat")
