"""Command handlers for protection management."""

from __future__ import annotations

import logging
import time
from collections import defaultdict
from copy import deepcopy
from typing import Any

from config import ADMIN_ROOM

from ..utils import (
    bare_jid,
    get_list_page_size,
    paginate_lines,
    parse_duration,
    resolve_page,
    safe_jid,
    wants_all_pages,
    without_all_pages_arg,
)
from .definitions import (
    PROTECTION_ACTIONS_BY_PROTECTION,
    PROTECTION_ALLOWED_ACTIONS,
    PROTECTION_DEFAULTS,
    PROTECTION_DISPLAY_ALIASES,
    PROTECTION_ORDER,
    canonical_protection_name,
)

log = logging.getLogger(__name__)
POLICY_CHANGE_NOTIFICATION_PROTECTION = "PolicyChangeNotification"

PROTECTION_INT_VALIDATION_KEYS = {
    "window_seconds",
    "max_messages",
    "max_similar",
    "min_length",
    "min_words",
    "tempban_seconds",
    "join_grace_seconds",
    "startup_grace_seconds",
    "rejoin_grace_seconds",
    "action_cooldown_seconds",
    "cooldown_seconds",
    "max_mentions",
    "max_joins",
    "threshold",
}
PROTECTION_BOOL_VALIDATION_KEYS = {
    "enabled",
    "redact",
    "members_only",
    "moderated",
    "notify_only",
    "notify_bans",
    "notify_unbans",
    "notify_config",
    "observe",
    "ignore_member_affiliations",
}
PROTECTION_LIST_OF_STR_VALIDATION_KEYS = {"words", "reporters"}


class ProtectionCommandsMixin:
    async def cmd_protection_report(self, room: str, nick: str, args: list[str]) -> None:
        """Public/admin trusted reporter command: !report <nick|jid> [reason]."""
        protection = "TrustedReporters"
        if not self.protection_enabled(protection):
            return
        if not args:
            await self.bot_send_message(
                mto=room,
                mbody=f"❌ Usage: {self.command_prefix}report <nick|jid> [reason]",
                mtype="groupchat",
            )
            return
        rooms_to_scan = [room, *sorted(getattr(self, "protected_rooms", set()))]

        # _protection_actor_jid is provided by the shared protection/base mixin layer.
        # It resolves the real actor JID for permission checks when nick differs from JID.
        reporter = self._protection_actor_jid(room, nick)
        if not reporter or "@" not in str(reporter):
            for current_room in rooms_to_scan:
                info = self.occupants.get(current_room, {}).get(nick, {})
                if info.get("jid"):
                    reporter = info.get("jid")
                    break

        config = self.protection_config(protection)
        reporter_values = [str(item).strip() for item in config.get("reporters", [])]
        reporters = {bare_jid(item) for item in reporter_values if item}
        if bare_jid(str(reporter)) not in reporters:
            await self.bot_send_message(mto=room, mbody="❌ You are not a trusted reporter.", mtype="groupchat")
            return
        raw_target = args[0].strip()
        target = raw_target.lower() if "@" in raw_target else raw_target
        reason = " ".join(args[1:]).strip() or str(config.get("reason") or "trusted report")
        key = (room, target.lower())
        now = time.time()
        window = max(1, int(config.get("window_seconds", 900) or 900))
        reports = [entry for entry in self.protection_trusted_reports[key] if now - entry[0] <= window]
        if not any(entry[1] == bare_jid(str(reporter)) for entry in reports):
            reports.append((now, bare_jid(str(reporter)) or str(reporter), reason))
        self.protection_trusted_reports[key] = reports
        threshold = max(1, int(config.get("threshold", 2) or 2))
        if len(reports) < threshold:
            await self.bot_send_message(
                mto=room,
                mbody=f"✅ Report recorded for {safe_jid(target)} ({len(reports)}/{threshold}).",
                mtype="groupchat",
            )
            return
        target_nick = target
        target_jid = target if "@" in target else None
        action_room = room

        # Prefer current nick casing and JID when a reported nick/JID can be
        # resolved from live occupants. Reports are often sent in the admin room,
        # so scan protected rooms as well as the reporting room.
        seen_rooms: set[str] = set()
        for current_room in rooms_to_scan:
            if current_room in seen_rooms:
                continue
            seen_rooms.add(current_room)
            for n, info in self.occupants.get(current_room, {}).items():
                if "@" in target:
                    if info.get("jid") and bare_jid(info.get("jid")) == bare_jid(target):
                        target_nick = n
                        target_jid = info.get("jid")
                        action_room = current_room
                        break
                elif n.lower() == target.lower():
                    target_nick = n
                    target_jid = info.get("jid")
                    action_room = current_room
                    break
            else:
                continue
            break

        protected = False
        protect_reason = None
        admin_target_check = getattr(self, "is_protected_admin_target", None)
        if callable(admin_target_check):
            protected, protect_reason = await admin_target_check(
                target,
                nick=target_nick if target_nick != target else None,
                jid=target_jid,
            )
        elif action_room in getattr(self, "protected_rooms", set()):
            protected = self._protection_is_exempt(action_room, target_nick, target_jid)

        if protected:
            await self.bot_send_message(
                mto=room,
                mbody=f"❌ Target {safe_jid(target)} is exempt from protection actions."
                + (f" ({protect_reason})" if protect_reason else ""),
                mtype="groupchat",
            )
            self.protection_trusted_reports.pop(key, None)
            return

        await self._protection_apply_action(
            protection=protection,
            room=action_room,
            nick=target_nick,
            reason=reason,
            action=str(config.get("action", "tempban")),
            tempban_seconds=int(config.get("tempban_seconds", 86400) or 86400),
            redact=bool(config.get("redact", True)),
            details={"reports": len(reports), "threshold": threshold},
        )
        self.protection_trusted_reports.pop(key, None)

    async def _dispatch_protections_command(self, room: str, nick: str, args: list[str], cmd: str) -> None:
        """Admin command entry point for !protection/!protections."""
        if not args:
            await self.cmd_protections_list(room, [])
            return
        subcmd = args[0].lower()
        if subcmd == "list":
            await self.cmd_protections_list(room, args[1:])
            return
        if subcmd in {"enable", "disable"}:
            if len(args) < 2:
                await self.bot_send_message(
                    mto=room,
                    mbody=f"❌ Usage: {self.command_prefix}{cmd} {subcmd} <protection>",
                    mtype="groupchat",
                )
                return
            await self.cmd_protection_set_enabled(room, args[1], subcmd == "enable", nick)
            return
        if subcmd in {"show", "config"}:
            if len(args) < 2:
                await self.bot_send_message(
                    mto=room,
                    mbody=f"❌ Usage: {self.command_prefix}{cmd} {subcmd} <protection>",
                    mtype="groupchat",
                )
                return
            await self.cmd_protection_config(room, args[1])
            return
        if subcmd == "reset":
            if len(args) < 2:
                await self.bot_send_message(
                    mto=room,
                    mbody=f"❌ Usage: {self.command_prefix}{cmd} reset <protection>",
                    mtype="groupchat",
                )
                return
            await self.cmd_protection_reset(room, args[1], nick)
            return
        # Shorthand: !protections FloodSpamProtection config/show/reset/set key value
        name = args[0]
        action = args[1].lower() if len(args) >= 2 else "config"
        if action in {"config", "show"}:
            await self.cmd_protection_config(room, name)
            return
        if action == "reset":
            await self.cmd_protection_reset(room, name, nick)
            return
        if action in {"observe", "enforce"}:
            value = action == "observe"
            if len(args) >= 3:
                raw = args[2].lower()
                if raw in {"on", "true", "yes", "1"}:
                    value = True
                elif raw in {"off", "false", "no", "0"}:
                    value = False
                else:
                    await self.bot_send_message(mto=room, mbody=f"❌ Usage: {self.command_prefix}{cmd} <protection> observe <on|off>", mtype="groupchat")
                    return
            await self.cmd_protection_set_config(room, name, "observe", "true" if value else "false", nick)
            return
        if canonical_protection_name(name) == "TrustedReporters" and action in {"add", "remove", "delete", "rm", "del", "list"}:
            await self.cmd_trusted_reporters(room, action, args[2:], nick)
            return
        if action == "set":
            if len(args) < 4:
                await self.bot_send_message(
                    mto=room,
                    mbody=f"❌ Usage: {self.command_prefix}{cmd} <protection> set <key> <value>",
                    mtype="groupchat",
                )
                return
            await self.cmd_protection_set_config(room, name, args[2], " ".join(args[3:]), nick)
            return
        await self.bot_send_message(
            mto=room,
            mbody=(
                "Usage:\n"
                f"  {self.command_prefix}protections list [all|page|last]\n"
                f"  {self.command_prefix}protection enable <name>\n"
                f"  {self.command_prefix}protection disable <name>\n"
                f"  {self.command_prefix}protections <name> config\n"
                f"  {self.command_prefix}protections <name> set <key> <value>\n"
                f"  {self.command_prefix}protections <name> observe <on|off>"
            ),
            mtype="groupchat",
        )

    async def cmd_protections_list(self, room: str, args: list[str]) -> None:
        show_all = wants_all_pages(args)
        page_args = without_all_pages_arg(args)
        page = 1
        if page_args:
            if page_args[0].lower() == "last":
                page = -1
            else:
                try:
                    page = max(1, int(page_args[0]))
                except ValueError:
                    await self.bot_send_message(
                        mto=room,
                        mbody=f"❌ Usage: {self.command_prefix}protections list [all|page|last]",
                        mtype="groupchat",
                    )
                    return
        lines = []
        for name in PROTECTION_ORDER:
            config = self.protection_config(name)
            enabled = bool(config.get("enabled"))
            observe = bool(config.get("observe", False))
            if enabled and observe:
                icon = "👁️"
            else:
                icon = "🟢" if enabled else "🔴"
            state = "enabled" if enabled else "disabled"
            if observe:
                state += ", observe"
            alias = PROTECTION_DISPLAY_ALIASES.get(name, name)
            lines.append(f"{icon} ({state}) {name} [{alias}]")
        if show_all:
            body = "🛡️ Protections:\n" + "\n".join(lines)
        else:
            per_page = get_list_page_size(self)
            page = resolve_page(page, len(lines), per_page)
            page_lines, current_page, total_pages, total_items = paginate_lines(lines, page, per_page)
            body = (
                f"🛡️ Protections ({total_items}) - Page {current_page}/{total_pages}:\n"
                + "\n".join(page_lines)
            )
            if current_page < total_pages:
                body += f"\n\nUse {self.command_prefix}protections list {current_page + 1} for the next page."
            body += f"\n\nUse {self.command_prefix}protections list all for full output."
        await self.bot_send_message(mto=room, mbody=body, mtype="groupchat")

    async def cmd_protection_config(self, room: str, raw_name: str) -> None:
        name, error = self._resolve_protection_or_error(raw_name)
        if error:
            await self.bot_send_message(mto=room, mbody=error, mtype="groupchat")
            return
        config = self.protection_config(name)
        lines = [f"🛡️ {name} config:", ""]
        for key in sorted(config):
            lines.append(f"{key} = {config[key]!r}")
        lines.extend([
            "",
            f"Change: {self.command_prefix}protections {name} set <key> <value>",
        ])
        await self.bot_send_message(mto=room, mbody="\n".join(lines), mtype="groupchat")

    async def cmd_protection_reset(self, room: str, raw_name: str, nick: str) -> None:
        """Reset one protection to its built-in default config."""
        name, error = self._resolve_protection_or_error(raw_name)
        if error:
            await self.bot_send_message(mto=room, mbody=error, mtype="groupchat")
            return
        old = dict(self.protection_config(name))
        self.protections[name] = deepcopy(PROTECTION_DEFAULTS[name])
        await self.persist_protection(name)
        actor = self._actor_jid_from_room_nick(room, nick)
        await self._audit_protection_config_change(actor, name, "reset", self.protections[name], old=old)
        await self.bot_send_message(
            mto=room,
            mbody=f"✅ {name} reset to defaults.",
            mtype="groupchat",
        )

    async def cmd_trusted_reporters(self, room: str, action: str, args: list[str], nick: str) -> None:
        """Manage TrustedReporters.reporters without requiring raw JSON lists."""
        name = "TrustedReporters"
        config = self.protection_config(name)
        reporters = [bare_jid(item) for item in config.get("reporters", []) if bare_jid(item)]
        old = list(reporters)

        if action == "list":
            await self.cmd_trusted_reporters_list(room, args)
            return

        if not args:
            await self.bot_send_message(
                mto=room,
                mbody=f"❌ Usage: {self.command_prefix}protections reporters {action} <jid>",
                mtype="groupchat",
            )
            return

        reporter = bare_jid(args[0])
        if not reporter or "@" not in reporter:
            await self.bot_send_message(mto=room, mbody="❌ Reporter must be a valid bare JID.", mtype="groupchat")
            return

        changed = False
        if action == "add":
            if reporter not in reporters:
                reporters.append(reporter)
                reporters.sort()
                changed = True
            message = f"✅ Trusted reporter added: {safe_jid(reporter)}" if changed else f"ℹ️ Trusted reporter already exists: {safe_jid(reporter)}"
        else:
            if reporter in reporters:
                reporters.remove(reporter)
                changed = True
            message = f"✅ Trusted reporter removed: {safe_jid(reporter)}" if changed else f"ℹ️ Trusted reporter not configured: {safe_jid(reporter)}"

        if changed:
            config["reporters"] = reporters
            await self.persist_protection(name)
            actor = self._actor_jid_from_room_nick(room, nick)
            await self._audit_protection_config_change(actor, name, "reporters", reporters, old=old)

        await self.bot_send_message(mto=room, mbody=message, mtype="groupchat")

    async def cmd_trusted_reporters_list(self, room: str, args: list[str]) -> None:
        """List configured trusted reporter JIDs."""
        config = self.protection_config("TrustedReporters")
        reporters = [safe_jid(bare_jid(item)) for item in config.get("reporters", []) if bare_jid(item)]
        show_all = wants_all_pages(args)
        page_args = without_all_pages_arg(args)
        page = 1
        if page_args:
            if page_args[0].lower() == "last":
                page = -1
            else:
                try:
                    page = max(1, int(page_args[0]))
                except ValueError:
                    await self.bot_send_message(
                        mto=room,
                        mbody=f"❌ Usage: {self.command_prefix}protections reporters list [all|page|last]",
                        mtype="groupchat",
                    )
                    return
        if not reporters:
            await self.bot_send_message(mto=room, mbody="🛡️ Trusted reporters: none configured.", mtype="groupchat")
            return
        if show_all:
            body = "🛡️ Trusted reporters:\n" + "\n".join(f"- {reporter}" for reporter in reporters)
        else:
            per_page = get_list_page_size(self)
            page = resolve_page(page, len(reporters), per_page)
            page_lines, current_page, total_pages, total_items = paginate_lines([f"- {reporter}" for reporter in reporters], page, per_page)
            body = f"🛡️ Trusted reporters ({total_items}) - Page {current_page}/{total_pages}:\n" + "\n".join(page_lines)
            if current_page < total_pages:
                body += f"\n\nUse {self.command_prefix}protections reporters list {current_page + 1} for the next page."
            body += f"\n\nUse {self.command_prefix}protections reporters list all for full output."
        await self.bot_send_message(mto=room, mbody=body, mtype="groupchat")

    async def cmd_protection_set_enabled(self, room: str, raw_name: str, enabled: bool, nick: str) -> None:
        name, error = self._resolve_protection_or_error(raw_name)
        if error:
            await self.bot_send_message(mto=room, mbody=error, mtype="groupchat")
            return
        old = bool(self.protection_config(name).get("enabled", False))
        value = bool(enabled)
        if old == value:
            await self.bot_send_message(
                mto=room,
                mbody=f"ℹ️ {name} is already {'enabled' if value else 'disabled'}.",
                mtype="groupchat",
            )
            return
        self.protection_config(name)["enabled"] = value
        await self.persist_protection(name)
        actor = self._actor_jid_from_room_nick(room, nick)
        await self._audit_protection_config_change(actor, name, "enabled", value, old=old)
        await self.bot_send_message(
            mto=room,
            mbody=f"✅ {name} {'enabled' if enabled else 'disabled'}.",
            mtype="groupchat",
        )

    def _parse_protection_value(self, raw_value: str) -> Any:
        """Parse protection config values using ordered fallbacks.

        Order: duration -> custom config parser -> bool -> int -> raw string.
        Earlier parser failures are expected for non-matching value shapes and
        intentionally fall through to the next parser.

        Optional hook interface:
            parse_config_value(text: str) -> Any

        If ``self.parse_config_value`` exists and is callable, it is invoked
        after duration parsing fails and before bool/int/string fallbacks.
        Implementations should accept a stripped string and return a
        parsed/normalized value for custom config value formats. When no
        special parsing applies, they should return the original or normalized
        string. Implementations should not raise for ordinary "no match"
        cases. Raised exceptions are treated as hard errors; this method logs
        them and re-raises so callers can fail loudly instead of silently
        accepting a bad config value.
        """
        text = str(raw_value).strip()
        try:
            value = parse_duration(text)
            log.debug("Parsed protection value as duration: raw=%r parsed=%r", raw_value, value)
            return value
        except ValueError:
            log.debug("Protection value is not a duration, falling back: raw=%r", raw_value)

        parser = getattr(self, "parse_config_value", None)
        if callable(parser):
            try:
                value = parser(text)
            except Exception:
                log.exception(
                    "Custom protection value parser failed: raw=%r normalized=%r",
                    raw_value,
                    text,
                )
                raise
            log.debug("Parsed protection value with custom parser: raw=%r parsed=%r", raw_value, value)
            return value

        lowered = text.lower()
        if lowered == "true":
            log.debug("Parsed protection value as bool: raw=%r parsed=True", raw_value)
            return True
        if lowered == "false":
            log.debug("Parsed protection value as bool: raw=%r parsed=False", raw_value)
            return False
        try:
            value = int(text)
            log.debug("Parsed protection value as int: raw=%r parsed=%r", raw_value, value)
            return value
        except ValueError:
            log.debug("Leaving protection value as string after fallbacks: raw=%r", raw_value)
            return text

    async def cmd_protection_set_config(self, room: str, raw_name: str, key: str, raw_value: str, nick: str) -> None:
        name, error = self._resolve_protection_or_error(raw_name)
        if error:
            await self.bot_send_message(mto=room, mbody=error, mtype="groupchat")
            return
        config = self.protection_config(name)
        key = key.strip().lower()
        if key not in config:
            await self.bot_send_message(
                mto=room,
                mbody=f"❌ Unknown config key for {name}: {key}",
                mtype="groupchat",
            )
            return
        if key == "enabled":
            value = self._parse_protection_value(raw_value)
            if not isinstance(value, bool):
                await self.bot_send_message(mto=room, mbody="❌ enabled must be True or False.", mtype="groupchat")
                return
        else:
            value = self._parse_protection_value(raw_value)
        ok, error_text = self._validate_protection_config_value(key, value, protection=name)
        if not ok:
            await self.bot_send_message(mto=room, mbody=f"❌ {error_text}", mtype="groupchat")
            return
        old = config.get(key)
        if old == value:
            await self.bot_send_message(
                mto=room,
                mbody=f"ℹ️ {name}.{key} is already {value!r}.",
                mtype="groupchat",
            )
            return
        config[key] = value
        await self.persist_protection(name)
        actor = self._actor_jid_from_room_nick(room, nick)
        await self._audit_protection_config_change(actor, name, key, value, old=old)
        await self.bot_send_message(
            mto=room,
            mbody=f"✅ {name}.{key} updated: {old!r} → {value!r}",
            mtype="groupchat",
        )

    def _validate_protection_config_value(
        self,
        key: str,
        value: Any,
        *,
        protection: str | None = None,
    ) -> tuple[bool, str]:
        """Validate a protection config value for a specific key.

        Returns:
            tuple[bool, str]: ``(is_valid, error_message)``.
            ``is_valid`` is ``True`` when the value passes all checks for
            ``key``. ``error_message`` is empty when valid, otherwise contains
            a user-facing explanation of the validation failure.

        Validation rules:
            - Integer threshold/window/count keys (for example
              ``window_seconds``, ``max_messages``, ``threshold``,
              ``rejoin_grace_seconds``, ``action_cooldown_seconds``) must be
              positive integers (>= 1).
            - Boolean toggle keys (for example ``enabled``, ``redact``,
              ``members_only``, ``notify_config``) must be ``bool``.
            - ``similarity_percent`` must satisfy its configured numeric range
              check.
            - ``action`` must be one of the allowed actions for the selected
              protection, falling back to global allowed actions when
              unspecified.
            - ``words`` and ``reporters`` must be lists of strings.
        """
        if key in PROTECTION_INT_VALIDATION_KEYS:
            if not isinstance(value, int) or value < 1:
                return False, f"{key} must be a positive integer."
        if key in PROTECTION_BOOL_VALIDATION_KEYS:
            if not isinstance(value, bool):
                return False, f"{key} must be True or False."
        if key == "similarity_percent":
            if not isinstance(value, int) or not 1 <= value <= 100:
                return False, "similarity_percent must be an integer from 1 to 100."
        if key == "action":
            allowed_actions = PROTECTION_ACTIONS_BY_PROTECTION.get(
                protection or "",
                PROTECTION_ALLOWED_ACTIONS,
            )
            normalized_value = value.lower() if isinstance(value, str) else str(value).lower()
            if normalized_value not in allowed_actions:
                return False, f"action must be one of: {', '.join(sorted(allowed_actions))}."
        if key in PROTECTION_LIST_OF_STR_VALIDATION_KEYS:
            if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
                return False, f"{key} must be a list of strings."
        return True, ""

    async def _audit_protection_config_change(
        self,
        actor: str | None,
        protection: str,
        key: str,
        value: Any,
        *,
        old: Any = None,
    ) -> None:
        if hasattr(self, "audit_event"):
            try:
                await self.audit_event(
                    "protection_config_changed",
                    actor=actor or "unknown",
                    target_type="protection",
                    target=protection,
                    comment=f"{key}: {old!r} -> {value!r}",
                    details={"protection": protection, "key": key, "old": old, "new": value},
                )
            except Exception as exc:
                log.debug("Failed to audit protection config change: %s", exc)
        if (
            self.protection_enabled(POLICY_CHANGE_NOTIFICATION_PROTECTION)
            and self.protection_config(POLICY_CHANGE_NOTIFICATION_PROTECTION).get("notify_config", True)
        ):
            await self.bot_send_message(
                mto=ADMIN_ROOM,
                mbody=f"📣 Protection config changed\nProtection: {protection}\nKey: {key}\nActor: {safe_jid(actor or 'unknown')}",
                mtype="groupchat",
            )
