"""Configuration display and runtime reload commands."""

import logging

from config import DB_FILE, JID, NICK

from ._version import __version__
from .config_utils import get_config_resource

log = logging.getLogger(__name__)


class ConfigCommandMixin:
    async def _cmd_config(self, room: str) -> None:
        config_lines = ["📋 Current Bot Configuration:\n"]

        config_lines.append(f"🤖 Bot Version: {__version__}")
        config_lines.append(f"💾 Database: {DB_FILE}")
        config_lines.append(f"🔐 JID: {JID}")
        config_lines.append(f"📦 Resource: {get_config_resource() or 'None'}")
        config_lines.append(f"👤 Nick: {NICK}")
        config_lines.append("")
        config_lines.append(f"⌨️ Command Prefix: {self.command_prefix}")
        config_lines.append(f"🧱 Structured Event Logs: {self.structured_event_logs}")
        config_lines.append(f"🧾 Audit Log: {self.audit_log_enabled} ({self.audit_log_retention_days}d retention)")
        config_lines.append(f"📢 Announce Startup: {self.announce_startup}")
        config_lines.append(f"📊 Announce Sync Details: {self.announce_sync_details}")
        config_lines.append(f"📣 Show Bans in MUC: {self.show_ban_in_muc}")
        config_lines.append(f"✅ Allow User Commands: {self.allow_user_cmds}")
        config_lines.append("")
        config_lines.append(f"⏰ Health Check Interval: {self.health_check_interval}s")
        config_lines.append(f"⏱️ Unban Check Interval: {self.unban_check_interval}s")
        config_lines.append(f"📅 Max Tempban Days: {self.max_tempban_days}")
        config_lines.append(f"🚦 Public Command Rate Limit: {self.public_command_rate_limit_max}/{self.public_command_rate_limit_window}s")
        config_lines.append(f"🔌 MUC Write Semaphore: {self.muc_write_limit}")
        config_lines.append("")
        config_lines.append(f"🛡️ RTBL Enabled: {self.rtbl_enabled}")
        config_lines.append(f"💾 RTBL Persist Bans: {getattr(self, 'rtbl_persist_bans', False)}")
        config_lines.append(f"📢 RTBL Announce: {self.rtbl_announce}")
        config_lines.append(f"🔄 RTBL Refresh Interval: {self.rtbl_refresh_interval}s" if self.rtbl_refresh_interval > 0 else "🔄 RTBL Refresh: disabled")
        config_lines.append(f"📡 RTBL Publish Enabled: {getattr(self, 'rtbl_publish_enabled', False)}")
        if getattr(self, "rtbl_publish_enabled", False):
            config_lines.append(f"   Service:     {self.rtbl_publish_service}")
            config_lines.append(f"   JID node:    {self.rtbl_publish_jid_node}")
            config_lines.append(f"   Domain node: {self.rtbl_publish_domain_node}")
        config_lines.append("")
        config_lines.append(f"🔄 Version Check Enabled: {self.version_check_enabled}")
        config_lines.append(f"🕒 Version Check Interval: {self.version_check_interval}s")
        config_lines.append(f"🌐 Version Check URL: {self.version_check_url or 'None'}")

        self.send_message(
            mto=room,
            mbody="\n".join(config_lines),
            mtype="groupchat"
        )


    async def _cmd_reloadconfig(self, room: str) -> None:
        try:
            changes, errors, warnings = await self.reload_runtime_config()

            if errors:
                msg = self._format_config_validation(errors, warnings)
                self.send_message(
                    mto=room,
                    mbody=f"❌ Config reload aborted. Old config is still active.\n\n{msg}",
                    mtype="groupchat"
                )
                log.error("Config reload aborted: %s", errors)
                return

            lines = ["✅ Config reloaded successfully."]

            if warnings:
                lines.append("\n⚠️ Warnings:")
                lines.extend(f"- {w}" for w in warnings)

            if changes:
                lines.append("\nChanged:")
                lines.extend(changes)
            else:
                lines.append("\nNo runtime config changes detected.")

            self.send_message(
                mto=room,
                mbody="\n".join(lines),
                mtype="groupchat"
            )
            log.info("Config reloaded at runtime. Changes: %s", changes or "none")
        except Exception as e:
            self.send_message(
                mto=room,
                mbody=f"❌ Failed to reload config: {e}",
                mtype="groupchat"
            )
            log.error("Failed to reload config: %s", e)
