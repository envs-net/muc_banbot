"""Action execution helpers for triggered protections."""

from __future__ import annotations

import inspect
import logging
import time
from typing import Any

from config import ADMIN_ROOM

try:
    from slixmpp.exceptions import IqError, IqTimeout
except ImportError:  # pragma: no cover - keeps pure unit tests importable without slixmpp
    class IqError(Exception):
        pass

    class IqTimeout(Exception):
        pass

from ..utils import safe_jid
from .definitions import PROTECTION_ALLOWED_ACTIONS

log = logging.getLogger(__name__)


class ProtectionActionsMixin:
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

    def _protection_action_cooldown_seconds(self, config: dict[str, Any]) -> int:
        """Return the short duplicate-action cooldown for message bursts."""
        return max(0, int(config.get("action_cooldown_seconds", 5) or 0))

    def _protection_action_on_cooldown(self, room: str, target: str, now: float) -> bool:
        """Return True when this target recently received a protection action."""
        key = (room, str(target).lower())
        until = getattr(self, "protection_action_cooldowns", {}).get(key, 0)
        if until > now:
            log.debug(
                "Protection action suppressed in %s for %s: cooldown active for %.1fs",
                room,
                target,
                until - now,
            )
            return True
        if until:
            self.protection_action_cooldowns.pop(key, None)
        return False

    def _protection_set_action_cooldown(self, room: str, target: str, seconds: int, now: float) -> None:
        """Set a short duplicate-action cooldown for this room/target pair."""
        if seconds <= 0:
            return
        self.protection_action_cooldowns[(room, str(target).lower())] = now + seconds

    async def _protection_redact_target_messages(
        self,
        target: str,
        reason: str,
        actor: str,
        *,
        title: str = "Auto-redaction completed after protection action",
    ) -> None:
        """Run announced auto-redaction for all indexed messages from a protected target."""
        if not getattr(self, "redaction_enabled", False):
            return
        if not target or "@" not in str(target) or str(target).startswith("*."):
            return
        redact_jid_messages = getattr(self, "redact_jid_messages", None)
        if not callable(redact_jid_messages):
            return
        try:
            await redact_jid_messages(
                target,
                reason=reason,
                actor=actor,
                announce=True,
                title=title,
            )
        except Exception as exc:
            log.warning("Protection target redaction failed for %s: %s", target, exc)

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
        now = time.time()
        punitive_action = action in {"kick", "tempban", "ban"}
        if punitive_action:
            cooldown_seconds = self._protection_action_cooldown_seconds(config)
            if self._protection_action_on_cooldown(room, target, now):
                return
            self._protection_set_action_cooldown(room, target, cooldown_seconds, now)

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
            await self.ban_all(target, until, actor, reason, auto_redact=False)
        elif action == "ban":
            await self.ban_all(target, None, actor, reason, auto_redact=False)

        if redact_enabled and punitive_action:
            title = (
                "Auto-redaction completed after ban"
                if action in {"tempban", "ban"}
                else "Auto-redaction completed after protection action"
            )
            await self._protection_redact_target_messages(
                target,
                reason,
                actor,
                title=title,
            )

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

    def _protection_room_config_value(self, value: Any) -> str:
        """Return a MUC data-form compatible value for room config fields."""
        if isinstance(value, bool):
            return "1" if value else "0"
        return str(value)

    def _protection_update_room_config_form(self, form: Any, fields: dict[str, Any]) -> Any:
        """Update a Slixmpp XEP-0004 room config form with the requested fields."""
        values = {key: self._protection_room_config_value(value) for key, value in fields.items()}
        set_type = getattr(form, "set_type", None)
        if callable(set_type):
            set_type("submit")
        else:
            try:
                form["type"] = "submit"
            except Exception:
                log.debug("Room config form does not expose a writable type field")

        set_values = getattr(form, "set_values", None) or getattr(form, "setValues", None)
        if callable(set_values):
            set_values(values)
            return form

        for key, value in values.items():
            try:
                form[key] = value
            except Exception:
                log.debug("Room config form does not expose field %s via item assignment", key)
        return form

    async def _protection_lockdown_room(self, room: str, config: dict[str, Any], reason: str) -> bool:
        """Best-effort set members-only + moderated during a join wave."""
        if not self.is_bot_admin_or_owner(room):
            return False
        fields: dict[str, Any] = {}
        if bool(config.get("members_only", True)):
            fields["muc#roomconfig_membersonly"] = True
        if bool(config.get("moderated", True)):
            fields["muc#roomconfig_moderatedroom"] = True
        if not fields:
            return False
        try:
            async with self.muc_write_semaphore:
                muc_plugin = self.plugin["xep_0045"]
                get_room_config = getattr(muc_plugin, "get_room_config", None)
                set_room_config = getattr(muc_plugin, "set_room_config", None)
                if not callable(set_room_config):
                    log.warning("MUC plugin has no set_room_config helper; cannot lockdown %s", room)
                    return False

                # Slixmpp expects set_room_config() to receive a filled XEP-0004
                # Form, not a plain dict.  Fetch the current form first so we
                # preserve unrelated room settings while changing only the
                # lockdown fields.
                if callable(get_room_config):
                    form_result = get_room_config(room)
                    form = await form_result if inspect.isawaitable(form_result) else form_result
                    payload = self._protection_update_room_config_form(form, fields)
                else:
                    # Unit-test/dummy fallback for plugins that intentionally
                    # accept a dict.  Real Slixmpp has get_room_config().
                    payload = {key: self._protection_room_config_value(value) for key, value in fields.items()}

                result = set_room_config(room, payload)
                if inspect.isawaitable(result):
                    await result
            log.warning("Protection lockdown applied in %s: %s", room, reason)
            return True
        except Exception as exc:
            log.warning("Protection lockdown failed in %s: %s", room, exc)
            return False
