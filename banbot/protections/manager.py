"""Protection runtime, persistence, command handling, and event hooks."""

from __future__ import annotations

from copy import deepcopy

import inspect
import json
import logging
import time
from collections import defaultdict, deque
from typing import Any

from config import ADMIN_ROOM
try:
    from slixmpp.exceptions import IqError, IqTimeout
except ImportError:  # pragma: no cover - keeps pure unit tests importable without slixmpp
    class IqError(Exception):
        pass

    class IqTimeout(Exception):
        pass

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
    PROTECTION_ALLOWED_ACTIONS,
    PROTECTION_DEFAULTS,
    PROTECTION_DISPLAY_ALIASES,
    PROTECTION_ORDER,
    canonical_protection_name,
    default_protection_config,
)
from .detection import body_contains_blocked_word, count_mentions, message_looks_like_media

log = logging.getLogger(__name__)


class ProtectionMixin:
    """Draupnir-inspired protection subsystem for protected XMPP MUCs."""

    def init_protection_state(self) -> None:
        """Initialize in-memory protection config and runtime counters."""
        self.protections: dict[str, dict[str, Any]] = default_protection_config()
        self.protection_message_windows: dict[tuple[str, str, str], deque[float]] = defaultdict(deque)
        self.protection_join_windows: dict[str, deque[float]] = defaultdict(deque)
        self.protection_joined_at: dict[tuple[str, str], float] = {}
        self.protection_first_message_seen: set[tuple[str, str]] = set()
        self.protection_trusted_reports: dict[tuple[str, str], list[tuple[float, str, str]]] = defaultdict(list)
        self.protection_room_lockdown_until: dict[str, float] = {}

    async def setup_protections_db(self) -> None:
        """Create persistence table for protection overrides."""
        if not getattr(self, "db", None):
            return
        await self.db.execute(
            """
            CREATE TABLE IF NOT EXISTS protections (
                name TEXT PRIMARY KEY,
                enabled INTEGER NOT NULL,
                config_json TEXT NOT NULL DEFAULT '{}',
                updated_at INTEGER NOT NULL DEFAULT (strftime('%s','now'))
            )
            """
        )
        await self.db.commit()

    async def load_protections(self) -> None:
        """Load protection enabled state/config overrides from SQLite."""
        self.init_protection_state()
        if not getattr(self, "db", None):
            return
        await self.setup_protections_db()
        async with self.db.execute("SELECT name, enabled, config_json FROM protections") as cursor:
            rows = await cursor.fetchall()

        for raw_name, enabled, config_json in rows:
            name = canonical_protection_name(str(raw_name)) or str(raw_name)
            if name not in self.protections:
                log.warning("Ignoring unknown persisted protection: %s", raw_name)
                continue
            config = dict(self.protections[name])
            try:
                loaded = json.loads(config_json or "{}")
            except json.JSONDecodeError:
                loaded = {}
            if isinstance(loaded, dict):
                config.update(loaded)
            config["enabled"] = bool(enabled)
            self.protections[name] = config

    async def persist_protection(self, name: str) -> None:
        """Persist one protection config override."""
        if not getattr(self, "db", None):
            return
        await self.setup_protections_db()
        config = dict(self.protections[name])
        enabled = bool(config.pop("enabled", False))
        await self.db.execute(
            """
            INSERT INTO protections (name, enabled, config_json, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(name) DO UPDATE SET
                enabled = excluded.enabled,
                config_json = excluded.config_json,
                updated_at = excluded.updated_at
            """,
            (name, 1 if enabled else 0, json.dumps(config, sort_keys=True), int(time.time())),
        )
        await self.db.commit()

    def protection_enabled(self, name: str) -> bool:
        """Return True when a protection is enabled."""
        return bool(self.protections.get(name, {}).get("enabled", False))

    def protection_config(self, name: str) -> dict[str, Any]:
        """Return the effective mutable config for a protection."""
        return self.protections.setdefault(name, dict(PROTECTION_DEFAULTS[name]))

    def _resolve_protection_or_error(self, name: str) -> tuple[str | None, str | None]:
        canonical = canonical_protection_name(name)
        if canonical and canonical in self.protections:
            return canonical, None
        return None, f"❌ Unknown protection: {name}\nUse {self.command_prefix}protections list all."

    def _protection_actor_jid(self, room: str, nick: str) -> str | None:
        info = getattr(self, "occupants", {}).get(room, {}).get(nick)
        if info and info.get("jid"):
            return bare_jid(info.get("jid"))
        return nick

    def _protection_subject(self, room: str, nick: str) -> tuple[str | None, str]:
        info = getattr(self, "occupants", {}).get(room, {}).get(nick, {})
        jid = bare_jid(info.get("jid")) if info.get("jid") else None
        return jid, nick.lower()

    def _protection_known_nicks(self, room: str) -> list[str]:
        """Return known room nicks from BanBot state and the XEP-0045 roster cache."""
        nicks: set[str] = set()

        occupants = getattr(self, "occupants", {}).get(room, {})
        if isinstance(occupants, dict):
            nicks.update(str(nick) for nick in occupants if str(nick).strip())

        try:
            muc_plugin = self.plugin["xep_0045"]
            rooms = getattr(muc_plugin, "rooms", {})
            room_roster = rooms.get(room, {}) if isinstance(rooms, dict) else {}
            if isinstance(room_roster, dict):
                nicks.update(str(nick) for nick in room_roster if str(nick).strip())
            elif isinstance(room_roster, (set, list, tuple)):
                nicks.update(str(nick) for nick in room_roster if str(nick).strip())
        except Exception:
            pass

        return sorted(nicks, key=str.lower)

    def _protection_is_exempt(self, room: str, nick: str, jid: str | None = None) -> bool:
        if room not in getattr(self, "protected_rooms", set()):
            return True
        if self.is_admin_or_owner(room, nick=nick, jid=jid):
            return True
        return False

    async def _protection_kick(self, room: str, nick: str, reason: str) -> bool:
        if not self.is_bot_admin_or_owner(room):
            return False
        try:
            async with self.muc_write_semaphore:
                await self.plugin["xep_0045"].set_role(
                    room=room,
                    nick=nick,
                    role="none",
                    reason=reason,
                )
            return True
        except (IqError, IqTimeout) as exc:
            log.warning("Protection kick failed for %s in %s: %s", nick, room, exc)
        except Exception as exc:
            log.warning("Protection kick failed for %s in %s: %s", nick, room, exc)
        return False

    async def _protection_redact_message(self, msg, reason: str, actor: str | None) -> None:
        if not getattr(self, "redaction_enabled", False):
            return
        stanza_id = None
        try:
            stanza_id = self._redaction_extract_stanza_id(msg)
            room = msg["from"].bare
        except Exception:
            return
        if not stanza_id:
            return
        try:
            await self._redaction_send_retract(room, stanza_id, reason)
        except Exception as exc:
            log.warning("Protection redaction failed for stanza %s in %s: %s", stanza_id, room, exc)
            return
        try:
            await self.flush_redaction_index()
            async with self.db.execute(
                "SELECT id FROM redaction_index WHERE room_jid = ? AND stanza_id = ?",
                (room, stanza_id),
            ) as cursor:
                row = await cursor.fetchone()
            if row:
                await self._redaction_mark_row(int(row[0]), actor, reason)
                await self.db.commit()
        except Exception as exc:
            log.debug("Protection redaction mark failed for stanza %s: %s", stanza_id, exc)

    async def _protection_apply_action(
        self,
        *,
        protection: str,
        room: str,
        nick: str,
        msg=None,
        action: str | None = None,
        reason: str | None = None,
        tempban_seconds: int | None = None,
        redact: bool | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        """Apply a configured protection action to one sender."""
        config = self.protection_config(protection)
        action = str(action or config.get("action", "notify")).lower().strip()
        if action not in PROTECTION_ALLOWED_ACTIONS:
            action = "notify"
        reason = str(reason or config.get("reason") or protection).strip()
        tempban_seconds = int(tempban_seconds or config.get("tempban_seconds", 3600) or 3600)
        redact_enabled = bool(config.get("redact", False) if redact is None else redact)
        jid, normalized_nick = self._protection_subject(room, nick)
        actor = f"protection:{protection}"
        target = jid or normalized_nick

        if redact_enabled and msg is not None:
            await self._protection_redact_message(msg, reason, actor)

        if action in {"notify", "warn"}:
            await self.bot_send_message(
                mto=ADMIN_ROOM,
                mbody=(
                    f"🛡️ {protection} triggered\n"
                    f"Room: {room}\n"
                    f"Target: {safe_jid(target)}\n"
                    f"Action: {action}\n"
                    f"Reason: {reason}"
                ),
                mtype="groupchat",
            )
            if action == "warn":
                await self.bot_send_message(
                    mto=room,
                    mbody=f"⚠️ {nick}: {reason}",
                    mtype="groupchat",
                )
        elif action == "kick":
            await self._protection_kick(room, nick, reason)
        elif action == "tempban":
            until = int(time.time()) + max(1, tempban_seconds)
            await self.ban_all(target, until, actor, reason)
        elif action == "ban":
            await self.ban_all(target, None, actor, reason)

        await self._audit_protection_event(
            protection,
            room,
            target,
            action,
            reason,
            details=details or {},
        )

    async def _audit_protection_event(
        self,
        protection: str,
        room: str,
        target: str,
        action: str,
        reason: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        if not hasattr(self, "audit_event"):
            return
        try:
            await self.audit_event(
                "protection_triggered",
                actor=f"protection:{protection}",
                room=room,
                target_type="jid" if "@" in str(target) else "nick",
                target=target,
                jid=target if "@" in str(target) else None,
                nick=None if "@" in str(target) else target,
                comment=reason,
                details={"protection": protection, "action": action, **(details or {})},
            )
        except Exception as exc:
            log.debug("Failed to audit protection event for %s: %s", protection, exc)

    async def protection_on_join(self, room: str, nick: str, jid: str | None = None) -> None:
        """Run join-based protections for a MUC presence join."""
        if self._protection_is_exempt(room, nick, jid):
            return

        now = time.time()
        subject = bare_jid(jid) if jid else nick.lower()
        self.protection_joined_at[(room, subject)] = now
        self.protection_first_message_seen.discard((room, subject))

        protection = "JoinWaveShortCircuitProtection"
        if not self.protection_enabled(protection):
            return
        config = self.protection_config(protection)
        window = max(1, int(config.get("window_seconds", 60) or 60))
        max_joins = max(1, int(config.get("max_joins", 8) or 8))
        joins = self.protection_join_windows[room]
        joins.append(now)
        while joins and now - joins[0] > window:
            joins.popleft()
        if len(joins) > max_joins:
            await self._protection_handle_join_wave(room, len(joins), config)

    async def _protection_handle_join_wave(self, room: str, join_count: int, config: dict[str, Any]) -> None:
        protection = "JoinWaveShortCircuitProtection"
        now = time.time()
        lockdown_seconds = max(0, int(config.get("lockdown_seconds", 900) or 0))
        existing_until = self.protection_room_lockdown_until.get(room, 0)
        if existing_until > now:
            return
        if lockdown_seconds > 0:
            self.protection_room_lockdown_until[room] = now + lockdown_seconds

        reason = str(config.get("reason") or "join wave detected")
        if not bool(config.get("notify_only", False)):
            await self._protection_lockdown_room(room, config, reason)

        await self.bot_send_message(
            mto=ADMIN_ROOM,
            mbody=(
                f"🚨 {protection} triggered\n"
                f"Room: {room}\n"
                f"Joins in window: {join_count}\n"
                f"Action: {'notify only' if config.get('notify_only') else 'members-only/moderated'}\n"
                f"Reason: {reason}"
            ),
            mtype="groupchat",
        )
        await self._audit_protection_event(
            protection,
            room,
            room,
            "lockdown" if not config.get("notify_only") else "notify",
            reason,
            details={"join_count": join_count},
        )

    async def _protection_lockdown_room(self, room: str, config: dict[str, Any], reason: str) -> bool:
        """Best-effort set members-only + moderated during a join wave."""
        if not self.is_bot_admin_or_owner(room):
            return False
        fields: dict[str, Any] = {}
        if bool(config.get("members_only", True)):
            fields["muc#roomconfig_membersonly"] = "1"
        if bool(config.get("moderated", True)):
            fields["muc#roomconfig_moderatedroom"] = "1"
        if not fields:
            return False
        try:
            async with self.muc_write_semaphore:
                muc_plugin = self.plugin["xep_0045"]
                set_room_config = getattr(muc_plugin, "set_room_config", None)
                if callable(set_room_config):
                    result = set_room_config(room, config=fields)
                    if inspect.isawaitable(result):
                        await result
                else:
                    log.warning("MUC plugin has no set_room_config helper; cannot lockdown %s", room)
                    return False
            log.warning("Protection lockdown applied in %s: %s", room, reason)
            return True
        except Exception as exc:
            log.warning("Protection lockdown failed in %s: %s", room, exc)
            return False

    async def protections_on_message(self, msg, room: str, nick: str, body: str) -> bool:
        """Run message-based protections. Return True when command handling should stop."""
        jid, normalized_nick = self._protection_subject(room, nick)
        if self._protection_is_exempt(room, nick, jid):
            return False
        subject = jid or normalized_nick
        now = time.time()

        handled = False
        if await self._protection_check_flood(msg, room, nick, subject, now):
            handled = True
        if await self._protection_check_first_media(msg, room, nick, subject, body, now):
            handled = True
        if await self._protection_check_mentions(msg, room, nick, body):
            handled = True
        if await self._protection_check_wordlist(msg, room, nick, subject, body, now):
            handled = True
        return handled

    async def _protection_check_flood(self, msg, room: str, nick: str, subject: str, now: float) -> bool:
        protection = "FloodSpamProtection"
        if not self.protection_enabled(protection):
            return False
        config = self.protection_config(protection)
        window = max(1, int(config.get("window_seconds", 60) or 60))
        max_messages = max(1, int(config.get("max_messages", 10) or 10))
        key = (protection, room, subject)
        hits = self.protection_message_windows[key]
        hits.append(now)
        while hits and now - hits[0] > window:
            hits.popleft()
        if len(hits) <= max_messages:
            return False
        await self._protection_apply_action(
            protection=protection,
            room=room,
            nick=nick,
            msg=msg,
            details={"messages": len(hits), "window_seconds": window},
        )
        hits.clear()
        return True

    async def _protection_check_first_media(self, msg, room: str, nick: str, subject: str, body: str, now: float) -> bool:
        protection = "FirstMessageMediaProtection"
        key = (room, subject)
        already_seen = key in self.protection_first_message_seen
        if not already_seen:
            self.protection_first_message_seen.add(key)
        if not self.protection_enabled(protection):
            return False
        if already_seen:
            return False
        joined_at = self.protection_joined_at.get(key)
        if joined_at is None:
            return False
        config = self.protection_config(protection)
        grace = max(0, int(config.get("join_grace_seconds", 600) or 0))
        if grace and now - joined_at > grace:
            return False
        if not message_looks_like_media(body):
            return False
        await self._protection_apply_action(
            protection=protection,
            room=room,
            nick=nick,
            msg=msg,
            details={"first_message": True},
        )
        return True

    async def _protection_check_mentions(self, msg, room: str, nick: str, body: str) -> bool:
        protection = "MentionLimitProtection"
        if not self.protection_enabled(protection):
            return False
        config = self.protection_config(protection)
        limit = max(1, int(config.get("max_mentions", 5) or 5))
        nicks = [
            known_nick
            for known_nick in self._protection_known_nicks(room)
            if known_nick.lower() != nick.lower()
        ]
        mention_count = count_mentions(body, nicks)
        log.debug(
            "%s checked in %s: nick=%s mentions=%d limit=%d known_nicks=%d",
            protection,
            room,
            nick,
            mention_count,
            limit,
            len(nicks),
        )
        if mention_count <= limit:
            return False
        await self._protection_apply_action(
            protection=protection,
            room=room,
            nick=nick,
            msg=msg,
            details={"mention_count": mention_count, "max_mentions": limit},
        )
        return True

    async def _protection_check_wordlist(self, msg, room: str, nick: str, subject: str, body: str, now: float) -> bool:
        protection = "WordListNewJoinerProtection"
        if not self.protection_enabled(protection):
            return False
        config = self.protection_config(protection)
        words = list(config.get("words", []) or [])
        if not words:
            return False
        joined_at = self.protection_joined_at.get((room, subject))
        if joined_at is None:
            return False
        grace = max(0, int(config.get("join_grace_seconds", 900) or 0))
        if grace and now - joined_at > grace:
            return False
        word = body_contains_blocked_word(body, words)
        if not word:
            return False
        reason = f"{config.get('reason') or 'blocked word from new joiner'}: {word}"
        await self._protection_apply_action(
            protection=protection,
            room=room,
            nick=nick,
            msg=msg,
            reason=reason,
            details={"word": word},
        )
        return True

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
        reporter = self._protection_actor_jid(room, nick)
        config = self.protection_config(protection)
        reporters = {bare_jid(str(item)) for item in config.get("reporters", []) if str(item).strip()}
        if bare_jid(str(reporter)) not in reporters:
            await self.bot_send_message(mto=room, mbody="❌ You are not a trusted reporter.", mtype="groupchat")
            return
        target = args[0].lower()
        reason = " ".join(args[1:]).strip() or str(config.get("reason") or "trusted report")
        key = (room, target)
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
        # If a JID was reported, prefer current nick if known for redaction/action context.
        if "@" in target:
            for n, info in self.occupants.get(room, {}).items():
                if info.get("jid") and bare_jid(info.get("jid")) == bare_jid(target):
                    target_nick = n
                    break
        await self._protection_apply_action(
            protection=protection,
            room=room,
            nick=target_nick,
            reason=reason,
            action=str(config.get("action", "tempban")),
            tempban_seconds=int(config.get("tempban_seconds", 86400) or 86400),
            redact=bool(config.get("redact", True)),
            details={"reports": len(reports), "threshold": threshold},
        )
        self.protection_trusted_reports.pop(key, None)

    async def notify_policy_change(
        self,
        event: str,
        *,
        actor: str | None = None,
        target: str | None = None,
        room: str | None = None,
        comment: str | None = None,
    ) -> None:
        """Send admin-room notification for policy changes when enabled."""
        protection = "PolicyChangeNotification"
        if not self.protection_enabled(protection):
            return
        config = self.protection_config(protection)
        if event.startswith("ban") and not bool(config.get("notify_bans", True)):
            return
        if event.startswith("unban") and not bool(config.get("notify_unbans", True)):
            return
        await self.bot_send_message(
            mto=ADMIN_ROOM,
            mbody=(
                "📣 Policy change\n"
                f"Event: {event}\n"
                f"Target: {safe_jid(target or 'unknown')}\n"
                f"Actor: {safe_jid(actor or 'unknown')}"
                + (f"\nRoom: {room}" if room else "")
                + (f"\nComment: {comment}" if comment else "")
            ),
            mtype="groupchat",
        )

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
                f"  {self.command_prefix}protections <name> set <key> <value>"
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
            icon = "🟢" if config.get("enabled") else "🔴"
            state = "enabled" if config.get("enabled") else "disabled"
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
        self.protection_config(name)["enabled"] = bool(enabled)
        await self.persist_protection(name)
        actor = self._actor_jid_from_room_nick(room, nick)
        await self._audit_protection_config_change(actor, name, "enabled", bool(enabled))
        await self.bot_send_message(
            mto=room,
            mbody=f"✅ {name} {'enabled' if enabled else 'disabled'}.",
            mtype="groupchat",
        )

    def _parse_protection_value(self, raw_value: str) -> Any:
        text = str(raw_value).strip()
        if text.endswith(("s", "m", "h", "d")) and text[:-1].isdigit():
            return parse_duration(text)
        parser = getattr(self, "parse_config_value", None)
        if callable(parser):
            return parser(text)
        lowered = text.lower()
        if lowered == "true":
            return True
        if lowered == "false":
            return False
        try:
            return int(text)
        except ValueError:
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
        ok, error_text = self._validate_protection_config_value(key, value)
        if not ok:
            await self.bot_send_message(mto=room, mbody=f"❌ {error_text}", mtype="groupchat")
            return
        old = config.get(key)
        config[key] = value
        await self.persist_protection(name)
        actor = self._actor_jid_from_room_nick(room, nick)
        await self._audit_protection_config_change(actor, name, key, value, old=old)
        await self.bot_send_message(
            mto=room,
            mbody=f"✅ {name}.{key} updated: {old!r} → {value!r}",
            mtype="groupchat",
        )

    def _validate_protection_config_value(self, key: str, value: Any) -> tuple[bool, str]:
        if key in {"window_seconds", "max_messages", "tempban_seconds", "join_grace_seconds", "max_mentions", "max_joins", "lockdown_seconds", "threshold"}:
            if not isinstance(value, int) or value < 1:
                return False, f"{key} must be a positive integer or duration like 10m."
        if key in {"enabled", "redact", "members_only", "moderated", "notify_only", "notify_bans", "notify_unbans", "notify_config"}:
            if not isinstance(value, bool):
                return False, f"{key} must be True or False."
        if key == "action" and str(value).lower() not in PROTECTION_ALLOWED_ACTIONS:
            return False, f"action must be one of: {', '.join(sorted(PROTECTION_ALLOWED_ACTIONS))}."
        if key in {"words", "reporters"}:
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
        if self.protection_enabled("PolicyChangeNotification") and self.protection_config("PolicyChangeNotification").get("notify_config", True):
            await self.bot_send_message(
                mto=ADMIN_ROOM,
                mbody=f"📣 Protection config changed\nProtection: {protection}\nKey: {key}\nActor: {safe_jid(actor or 'unknown')}",
                mtype="groupchat",
            )
