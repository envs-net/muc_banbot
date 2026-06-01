"""Configuration display, runtime editing and reload commands."""

from __future__ import annotations

import logging
import pathlib
import re
from typing import Any

from ._version import __version__
from .config_utils import ConfigMixin

log = logging.getLogger(__name__)

_CONFIG_ASSIGNMENT_RE = re.compile(r"^\s*([A-Z][A-Z0-9_]*)\s*=")


class ConfigCommandMixin(ConfigMixin):
    # Inherit ConfigMixin helpers directly. This keeps lightweight test doubles working
    # without duplicating helper attributes on the mixin class.
    CONFIG_SAMPLE_SECTION_TITLES = {
        "CONFIG": "🪪 Bot Identity / Control",
        "CONNECTION": "🌐 Connection",
        "DATABASE / BACKUPS": "💾 Database / Backups",
        "MANAGED CSV EXPORTS": "📦 Managed CSV Exports",
        "VCARD SETTINGS": "🖼️ vCard Settings",
        "BOT SETTINGS": "⚙️ Bot Settings",
        "PERFORMANCE TUNING": "⚡ Performance Tuning",
        "LOGGING / AUDIT": "📜 Logging / Audit",
        "EVENT ALERTS": "🚨 Event Alerts",
        "OMEMO ENCRYPTION": "🔐 OMEMO Encryption",
        "RTBL (REAL-TIME BLOCK LIST)": "🛡️ RTBL",
        "VERSION CHECK": "🔎 Version Check",
        "REDACTION": "🧹 Redaction",
    }

    def _config_sample_paths(self) -> list[pathlib.Path]:
        """Return candidate config_sample.py paths for ordering !config output."""
        return [
            pathlib.Path("config_sample.py"),
            pathlib.Path(__file__).resolve().parent.parent / "config_sample.py",
        ]

    def _config_sample_sections(self) -> list[tuple[str, tuple[str, ...]]]:
        """Parse config_sample.py sections so !config cannot drift from the sample."""
        heading_re = re.compile(r"^#\s*=+\s*(.*?)\s*=+\s*$")

        for path in self._config_sample_paths():
            try:
                if not path.is_file():
                    continue

                sections: list[tuple[str, list[str]]] = []
                current_title: str | None = None
                current_keys: list[str] = []

                for line in path.read_text(encoding="utf-8").splitlines():
                    heading = heading_re.match(line)
                    if heading:
                        if current_title is not None and current_keys:
                            sections.append((current_title, current_keys))
                        raw_title = " ".join(heading.group(1).strip().split())
                        mapped_title = self.CONFIG_SAMPLE_SECTION_TITLES.get(
                            raw_title.upper(),
                            f"📦 {raw_title.title()}",
                        )
                        current_title = mapped_title
                        current_keys = []
                        continue

                    match = _CONFIG_ASSIGNMENT_RE.match(line)
                    if not match:
                        continue

                    if current_title is None:
                        current_title = "📦 Other"
                    key = match.group(1)
                    if key not in current_keys:
                        current_keys.append(key)

                if current_title is not None and current_keys:
                    sections.append((current_title, current_keys))

                if sections:
                    return [(title, tuple(keys)) for title, keys in sections]
            except OSError as exc:
                log.debug("Could not read config sample sections from %s: %s", path, exc)
        return []

    def _config_sample_key_order(self) -> dict[str, int]:
        """Read key order from config_sample.py so !config follows the sample file."""
        order: dict[str, int] = {}
        for _title, keys in self._config_sample_sections():
            for key in keys:
                if key not in order:
                    order[key] = len(order)
        return order

    def _ordered_config_output_sections(self) -> list[tuple[str, tuple[str, ...]]]:
        """Return display sections parsed from config_sample.py."""
        sections = self._config_sample_sections()
        if sections:
            return sections

        # Minimal fallback for environments where config_sample.py is unavailable.
        return [
            ("🪪 Bot Identity / Control", ("JID", "RESOURCE", "PASSWORD", "ADMIN_ROOM", "NICK")),
            ("🌐 Connection", ("CONNECT_HOST", "CONNECT_PORT", "CONNECT_DIRECT_TLS")),
            (
                "💾 Database / Backups",
                ("DB_FILE", "DB_BACKUP_ON_START", "DB_BACKUP_DIR", "DB_BACKUP_KEEP", "DB_BACKUP_INCLUDE_OMEMO"),
            ),
            ("📦 Managed CSV Exports", ("EXPORT_DIR", "EXPORT_KEEP")),
        ]

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

        for title, keys in self._ordered_config_output_sections():
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

        sample_order = self._config_sample_key_order()
        other_items = [
            (key, value, writable)
            for key, (value, writable) in items.items()
            if key not in emitted
        ]
        other_items.sort(key=lambda item: sample_order.get(item[0], 10_000))
        other_lines = [
            self._format_config_line(key, value, writable)
            for key, value, writable in other_items
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
            ok, message = await self.set_runtime_config_value(key, raw_value, actor=actor)
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
            ok, message = await self.unset_runtime_config_value(key, actor=actor)
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
            config_lines.append("")
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
                log.error("Config reload aborted with %d validation error(s)", len(errors))
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
            log.info("Config reloaded at runtime. Changed settings: %d", len(changes))
        except Exception as e:
            await self.bot_send_message(
                mto=room,
                mbody=f"❌ Failed to reload config: {e}",
                mtype="groupchat"
            )
            log.error("Failed to reload config")
