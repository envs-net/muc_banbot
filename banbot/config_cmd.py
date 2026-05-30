"""Configuration display, runtime editing and reload commands."""

from __future__ import annotations

import logging
from typing import Any

from ._version import __version__
from .config_utils import ConfigMixin

log = logging.getLogger(__name__)


class ConfigCommandMixin:
    # Reuse ConfigMixin helpers here so lightweight test doubles that only mix in
    # ConfigCommandMixin can still render/edit config without inheriting the full bot.
    CONFIG_KEYS = ConfigMixin.CONFIG_KEYS
    STARTUP_ONLY_CONFIG_KEYS = ConfigMixin.STARTUP_ONLY_CONFIG_KEYS
    CONFIG_SECRET_KEYS = ConfigMixin.CONFIG_SECRET_KEYS
    CONFIG_NEVER_WRITABLE_KEYS = ConfigMixin.CONFIG_NEVER_WRITABLE_KEYS
    _config_file_path = ConfigMixin._config_file_path
    _config_sample_path = ConfigMixin._config_sample_path
    _ordered_config_keys_from_sample = ConfigMixin._ordered_config_keys_from_sample
    _config_default_values_from_sample = ConfigMixin._config_default_values_from_sample
    get_ordered_config_items = ConfigMixin.get_ordered_config_items
    format_config_value_for_display = ConfigMixin.format_config_value_for_display
    parse_config_value = ConfigMixin.parse_config_value
    render_config_assignment = ConfigMixin.render_config_assignment
    update_config_file_assignment = ConfigMixin.update_config_file_assignment
    set_runtime_config_value = ConfigMixin.set_runtime_config_value
    unset_runtime_config_value = ConfigMixin.unset_runtime_config_value

    CONFIG_OUTPUT_SECTIONS = (
        ("💾 Storage", (
            "DB_FILE",
        )),
        ("🪪 Bot Identity", (
            "JID",
            "RESOURCE",
            "RESSOURCE",
            "PASSWORD",
            "NICK",
        )),
        ("🏠 Rooms", (
            "ADMIN_ROOM",
        )),
        ("🌐 Connection", (
            "CONNECT_HOST",
            "CONNECT_PORT",
            "CONNECT_DIRECT_TLS",
        )),
        ("🖼️ Profile / vCard", (
            "AVATAR_PATH",
            "VCARD_NICKNAME",
            "VCARD_FN",
            "VCARD_ORG",
            "VCARD_ROLE",
            "VCARD_URL",
            "VCARD_NOTE",
        )),
        ("⚙️ General", (
            "LOG_LEVEL",
            "COMMAND_PREFIX",
            "ANNOUNCE_STARTUP",
            "ANNOUNCE_SYNC_DETAILS",
        )),
        ("🧭 Command Access", (
            "ALLOW_USER_COMMANDS_IN_PROTECTED_ROOMS",
            "ALLOW_ADMIN_COMMANDS_IN_DMS",
            "PUBLIC_COMMAND_RATE_LIMIT_WINDOW",
            "PUBLIC_COMMAND_RATE_LIMIT_MAX",
        )),
        ("🛡️ Moderation", (
            "SHOW_BAN_IN_MUC",
            "ROOM_INVITES_ENABLED",
            "MAX_TEMPBAN_DAYS",
        )),
        ("🩺 Maintenance", (
            "HEALTH_CHECK_INTERVAL",
            "UNBAN_CHECK_INTERVAL",
        )),
        ("⚡ Performance", (
            "MUC_WRITE_SEMAPHORE",
        )),
        ("📜 Audit / Events", (
            "STRUCTURED_EVENT_LOGS",
            "AUDIT_LOG_ENABLED",
            "AUDIT_LOG_RETENTION_DAYS",
        )),
        ("🚨 Alerts", (
            "ALERT_ON_RECONNECT",
            "ALERT_ON_ADMIN_RIGHTS_LOST",
            "ALERT_ON_HEALTH_CHECK_FAILURE",
            "ALERT_ON_DB_STATS_FAILURE",
            "ALERT_ON_REDACTION_FAILURE",
            "ALERT_ON_DB_SIZE_MB",
            "ALERT_ON_RTBL_REFRESH_FAILURES",
            "ALERT_DEDUP_WINDOW",
        )),
        ("🔐 OMEMO", (
            "OMEMO_ENABLED",
            "OMEMO_STORAGE_FILE",
            "OMEMO_AUTO_ENCRYPT_ADMIN_ROOM",
            "OMEMO_PLAINTEXT_FALLBACK",
            "OMEMO_RESET_ON_IDENTITY_CHANGE",
        )),
        ("🛡️ RTBL", (
            "RTBL_ENABLED",
            "RTBL_ANNOUNCE",
            "RTBL_REFRESH_INTERVAL",
            "RTBL_PUBLISH_ENABLED",
            "RTBL_PUBLISH_SERVICE",
            "RTBL_PUBLISH_JID_NODE",
            "RTBL_PUBLISH_DOMAIN_NODE",
        )),
        ("🔎 Version Check", (
            "VERSION_CHECK_ENABLED",
            "VERSION_CHECK_INTERVAL",
            "VERSION_CHECK_URL",
        )),
        ("🧹 Redaction", (
            "REDACTION_ENABLED",
            "REDACTION_INDEX_RETENTION_DAYS",
            "REDACTION_AUTO_REASONS",
        )),
    )

    def _format_config_display_value(self, key: str, value: Any) -> str:
        if isinstance(value, (list, tuple)) and len(value) > 6:
            preview = ", ".join(repr(item) for item in value[:4])
            return f"[{preview}, ...] ({len(value)} items)"
        return self.format_config_value_for_display(key, value)

    def _format_config_line(self, key: str, value: Any, writable: bool) -> str:
        marker = "✏️" if writable else "🔒"
        return f"{marker} {key} = {self._format_config_display_value(key, value)}"

    def _sectioned_config_lines(self) -> list[str]:
        items = {key: (value, writable) for key, value, writable in self.get_ordered_config_items()}
        emitted: set[str] = set()
        lines: list[str] = []

        for title, keys in self.CONFIG_OUTPUT_SECTIONS:
            section_lines: list[str] = []
            for key in keys:
                if key not in items or key in emitted:
                    continue
                value, writable = items[key]
                section_lines.append(self._format_config_line(key, value, writable))
                emitted.add(key)

            if section_lines:
                if lines:
                    lines.append("")
                lines.append(title)
                lines.extend(section_lines)

        other_lines = [
            self._format_config_line(key, value, writable)
            for key, (value, writable) in items.items()
            if key not in emitted
        ]
        if other_lines:
            if lines:
                lines.append("")
            lines.append("📦 Other")
            lines.extend(other_lines)

        return lines

    async def _cmd_config(self, room: str, args: list[str] | None = None, actor: str | None = None) -> None:
        """Handle !config, !config show, !config set and !config unset."""
        args = args or []
        subcmd = args[0].lower() if args else "show"

        if subcmd in ("show", "list"):
            await self._cmd_config_show(room)
            return

        if subcmd == "set":
            if len(args) < 3:
                await self.bot_send_message(
                    mto=room,
                    mbody=(
                        f"❌ Usage: {self.command_prefix}config set <KEY> <value>\n"
                        f"Example: {self.command_prefix}config set LOG_LEVEL DEBUG"
                    ),
                    mtype="groupchat",
                )
                return
            key = args[1].upper()
            raw_value = " ".join(args[2:])
            ok, message = await self.set_runtime_config_value(key, raw_value)
            await self.bot_send_message(mto=room, mbody=message, mtype="groupchat")
            await self._audit_config_change(actor, "set", key, ok, message)
            return

        if subcmd == "unset":
            if len(args) != 2:
                await self.bot_send_message(
                    mto=room,
                    mbody=f"❌ Usage: {self.command_prefix}config unset <KEY>",
                    mtype="groupchat",
                )
                return
            key = args[1].upper()
            ok, message = await self.unset_runtime_config_value(key)
            await self.bot_send_message(mto=room, mbody=message, mtype="groupchat")
            await self._audit_config_change(actor, "unset", key, ok, message)
            return

        await self.bot_send_message(
            mto=room,
            mbody=(
                "Usage:\n"
                f"  {self.command_prefix}config show\n"
                f"  {self.command_prefix}config set <KEY> <value>\n"
                f"  {self.command_prefix}config unset <KEY>\n\n"
                "🔒 = restart-only or protected, ✏️ = runtime-writable"
            ),
            mtype="groupchat",
        )

    async def _cmd_config_show(self, room: str) -> None:
        config_lines = [f"📋 Current Bot Configuration (v{__version__})", ""]

        config_lines.append("🔒 = restart-only/protected, ✏️ = runtime-writable")
        config_lines.append("Password/secret values are hidden.")
        config_lines.append("")
        config_lines.extend(self._sectioned_config_lines())

        # Keep a compact operational summary after the full ordered config list.
        config_lines.append("")
        config_lines.append("🔐 OMEMO Runtime:")
        config_lines.append("   Reply mode: follows incoming command encryption")
        config_lines.append(f"   Auto-encrypt admin room: {getattr(self, 'omemo_auto_encrypt_admin_room', True)}")
        config_lines.append(f"   Plaintext fallback: {getattr(self, 'omemo_plaintext_fallback', False)}")
        config_lines.append(f"   Reset on identity change: {getattr(self, 'omemo_reset_on_identity_change', True)}")
        if getattr(self, "rtbl_publish_enabled", False):
            config_lines.append("📡 RTBL Publish Runtime:")
            config_lines.append(f"   Service:     {self.rtbl_publish_service}")
            config_lines.append(f"   JID node:    {self.rtbl_publish_jid_node}")
            config_lines.append(f"   Domain node: {self.rtbl_publish_domain_node}")

        config_lines.append("")
        config_lines.append(
            "Commands:\n"
            f"  {self.command_prefix}config set <KEY> <value>\n"
            f"  {self.command_prefix}config unset <KEY>\n"
            f"  {self.command_prefix}reloadconfig"
        )

        await self.bot_send_message(
            mto=room,
            mbody="\n".join(config_lines),
            mtype="groupchat",
        )

    async def _audit_config_change(self, actor: str | None, action: str, key: str, ok: bool, message: str) -> None:
        try:
            await self.audit_event(
                "config_changed" if ok else "config_change_failed",
                actor=actor or "unknown",
                details={"action": action, "key": key, "ok": ok, "message": message},
            )
        except Exception as exc:
            log.debug("Failed to audit config %s for %s: %s", action, key, exc)

    async def _cmd_reloadconfig(self, room: str) -> None:
        try:
            changes, errors, warnings = await self.reload_runtime_config()

            if errors:
                msg = self._format_config_validation(errors, warnings)
                await self.bot_send_message(
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

            await self.bot_send_message(
                mto=room,
                mbody="\n".join(lines),
                mtype="groupchat"
            )
            log.info("Config reloaded at runtime. Changes: %s", changes or "none")
        except Exception as e:
            await self.bot_send_message(
                mto=room,
                mbody=f"❌ Failed to reload config: {e}",
                mtype="groupchat"
            )
            log.error("Failed to reload config: %s", e)
