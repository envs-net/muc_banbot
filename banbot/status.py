"""Status command output."""

import asyncio
import logging
import os
import time

import psutil

from envs_xmpp_core.formatting import format_bytes

import config

from ._version import __version__
from .occupants import bot_room_status_line
from .protections.definitions import PROTECTION_DEFAULTS, PROTECTION_ORDER
from .protections.presentation import protection_status_line
from .status_health import collect_status_health_snapshot, status_health_messages
from .utils import human_time

log = logging.getLogger(__name__)


class StatusMixin:
    @staticmethod
    def human_size(num_bytes: int) -> str:
        return format_bytes(num_bytes, negative_label=None, max_unit="GiB")

    async def _cmd_status(self, room: str) -> None:
        now = int(time.time())

        # Collect passive health signals through the shared snapshot model.
        health = await collect_status_health_snapshot(self)
        problems, warnings, notes = status_health_messages(health)
        last_reconnect_time = getattr(self, "last_reconnect_time", None)

        rooms_health = health.check("rooms")
        protected_rooms = list(rooms_health.data.get("protected_rooms", ()))
        admins = list(rooms_health.data.get("admins", ()))
        db_stats = dict(health.check("database_stats").data.get("stats", {}))
        expired_ban_rows = int(db_stats.get("expired_ban_rows", 0) or 0)

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

        # backup / restore info
        if getattr(self, "last_database_backup_file", None):
            status_lines.append(f"\n💾 Last DB Backup: {self.last_database_backup_file}")
        if getattr(self, "last_database_restore_file", None):
            status_lines.append(f"♻️ Last DB Restore: {self.last_database_restore_file}")

        # version
        status_lines.append(f"\n🤖 Bot Version: {__version__}")
        if self.last_version_check_result:
            status_lines.append(f"🏷️ Latest Release Version: {self.last_version_check_result}\n")

        # uptime
        bot_uptime = now - int(self.bot_start_time)
        status_lines.append(f"⏱️ Bot Uptime: {human_time(bot_uptime)}")

        if self.server_connect_time:
            server_uptime = now - int(self.server_connect_time)
            status_lines.append(f"🌐 Server Connected: {human_time(server_uptime)}")

        # connection
        boundjid = getattr(self, "boundjid", None)
        connect_host = (
            getattr(config, "CONNECT_HOST", None)
            or getattr(boundjid, "host", None)
            or "JID domain"
        )
        connect_port = getattr(config, "CONNECT_PORT", 5222)
        connect_mode = "direct TLS" if getattr(config, "CONNECT_DIRECT_TLS", False) else "STARTTLS"
        status_lines.append(f"🌐 Connection: {connect_host}:{connect_port} ({connect_mode})")

        process = None
        try:
            process = psutil.Process(os.getpid())
        except Exception as e:
            log.debug("Could not create process info handle: %s", e)

        # mem info
        try:
            if process is not None:
                memory_info = process.memory_info()
                memory_mb = memory_info.rss / 1024 / 1024
                status_lines.append(f"💾 Memory Usage: {memory_mb:.1f} MB")
        except Exception as e:
            log.debug("Could not get memory info: %s", e)

        # cpu info
        try:
            if process is not None:
                loop = asyncio.get_running_loop()

                # psutil sampling runs in executor; use a short interval to avoid tying up a worker thread
                cpu_percent = await loop.run_in_executor(None, process.cpu_percent, 0.1)
                cpu_load = psutil.getloadavg()[0]
                cpu_count = psutil.cpu_count() or 1

                status_lines.append(f"🧠 CPU Usage: {cpu_percent:.1f}% (Process)")
                status_lines.append(f"⚙️ System Load: {cpu_load:.2f} ({cpu_count} cores)")
        except Exception as e:
            log.debug("Could not get CPU info: %s", e)

        # db info
        db_size = int(db_stats.get("db_size_bytes", 0) or 0)
        status_lines.append(f"💽 DB Size: {self.human_size(db_size)}")

        # redaction index info
        try:
            if hasattr(self, "flush_redaction_index"):
                await self.flush_redaction_index()

            redaction_total = 0
            redaction_redacted = 0
            if getattr(self, "db", None):
                async with self.db.execute(
                    """
                    SELECT
                        COUNT(*),
                        COALESCE(SUM(CASE WHEN redacted_at IS NOT NULL THEN 1 ELSE 0 END), 0)
                    FROM redaction_index
                    """
                ) as cursor:
                    row = await cursor.fetchone()
                    if row:
                        redaction_total = int(row[0] or 0)
                        redaction_redacted = int(row[1] or 0)
            if getattr(self, "redaction_enabled", False) or redaction_total > 0:
                status_lines.append(
                    f"🧹 Redaction Index: {redaction_total} tracked, {redaction_redacted} redacted"
                )
        except Exception as e:
            log.debug("Could not get redaction index stats: %s", e)

        # audit info
        audit_events = db_stats.get("audit_events", 0)
        status_lines.append(f"🧾 Audit Events: {audit_events} (retention: {self.audit_log_retention_days}d)")

        # affiliation query
        if self.admin_affiliation_query_forbidden_rooms:
            status_lines.append(
                f"\nℹ️ Admin protection fallback rooms: "
                f"{len(self.admin_affiliation_query_forbidden_rooms)}"
            )

        # ban info
        permanent_bans = db_stats.get("permanent_bans", 0)
        temporary_bans = db_stats.get("temporary_bans", 0)
        status_lines.append(f"\n📊 Active Bans: {permanent_bans} permanent, {temporary_bans} temporary")
        status_lines.append(f"🧹 Expired tempbans pending auto-unban: {expired_ban_rows}")

        # room invite info
        pending_invites = len(getattr(self, "pending_room_invites", {}) or {})
        status_lines.append(f"📨 Pending Room Invites: {pending_invites}")

        # rtbl
        if getattr(self, "rtbl_enabled", False):
            rtbl_hashes = len(getattr(self, "rtbl_hash_cache", {}))
            rtbl_domains = len(getattr(self, "rtbl_domain_cache", {}))
            rtbl_subscriptions = len(getattr(self, "rtbl_subscriptions", []))
            status_lines.append(f"\n🛡️ RTBL Entries: {rtbl_hashes} JID hashes, {rtbl_domains} domains")
            status_lines.append(f"📋 RTBL Subscriptions: {rtbl_subscriptions}")

        rtbl_publish_runtime_enabled = getattr(self, "rtbl_publish_enabled", False)
        rtbl_publish_config_enabled = getattr(
            self,
            "rtbl_publish_config_enabled",
            rtbl_publish_runtime_enabled or bool(getattr(self, "rtbl_publish_disabled_reason", None)),
        )
        if rtbl_publish_config_enabled or rtbl_publish_runtime_enabled:
            if rtbl_publish_runtime_enabled:
                status_lines.append("📡 RTBL Publish: enabled")
                if getattr(self, "rtbl_publish_sanity_check_ok", None) is True:
                    status_lines.append("   Sanity Check: ✅ OK")
                status_lines.append(f"   Service:     {self.rtbl_publish_service}")
                status_lines.append(f"   JID node:    {self.rtbl_publish_jid_node}")
                status_lines.append(f"   Domain node: {self.rtbl_publish_domain_node}")
            else:
                status_lines.append("📡 RTBL Publish: ⚠️ disabled at runtime (configured: enabled)")
                reason = getattr(self, "rtbl_publish_disabled_reason", None)
                if reason:
                    status_lines.append(f"   Reason: {reason}")

        # admins
        status_lines.append(
            "\n🛡️ Admins/Owners in Admin-Room:\n" + "\n".join(admins)
            if admins else "\n⚠️ No admins/owners found in Admin-Room."
        )

        # protected rooms
        if protected_rooms:
            preview_count = 10
            preview_rooms = protected_rooms[:preview_count]
            preview_lines = [
                bot_room_status_line(self, room_name)
                for room_name in preview_rooms
            ]

            status_lines.append(
                f"\n🔒 Protected Rooms ({len(protected_rooms)}):\n"
                + "\n".join(preview_lines)
            )

            remaining = len(protected_rooms) - len(preview_rooms)
            if remaining > 0:
                status_lines.append(
                    f"\n... and {remaining} more.\n"
                    f"Use {self.command_prefix}room list [page] to view all protected rooms."
                )
        else:
            status_lines.append("\n⚠️ No protected rooms configured.")

        # protection runtime state
        protection_configs = getattr(self, "protections", {}) or {}
        protection_lines = [
            protection_status_line(
                name,
                protection_configs.get(name, PROTECTION_DEFAULTS[name]),
            )
            for name in PROTECTION_ORDER
        ]
        status_lines.append("\n🛡️ Protections:\n" + "\n".join(protection_lines))

        await self.bot_send_message(mto=room, mbody="\n".join(status_lines), mtype="groupchat")
