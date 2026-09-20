"""Action execution helpers for triggered protections."""

from __future__ import annotations

import inspect
import logging
import time
from typing import TYPE_CHECKING, Any

from envs_xmpp_core.xmpp import iq_error_summary

from config import ADMIN_ROOM


class _FallbackIqError(Exception):
    pass


class _FallbackIqTimeout(Exception):
    pass


try:
    from slixmpp import exceptions as _slixmpp_exceptions
except ImportError:  # pragma: no cover - keeps pure unit tests importable without slixmpp
    _slixmpp_exceptions = None

if _slixmpp_exceptions is None:
    PROTECTION_IQ_EXCEPTIONS = (_FallbackIqError, _FallbackIqTimeout)
else:
    PROTECTION_IQ_EXCEPTIONS = (_slixmpp_exceptions.IqError, _slixmpp_exceptions.IqTimeout)

from ..utils import safe_jid
from .decision import (
    ProtectionDecision,
    ProtectionMatch,
    arbitrate_protection_match,
    protection_action_strength,
)
from .definitions import PROTECTION_ALLOWED_ACTIONS, ProtectionActionOutcome

log = logging.getLogger(__name__)


if TYPE_CHECKING:
    from ..contracts import ProtectionActionsMixinHost

    class _ProtectionActionsMixinContract(ProtectionActionsMixinHost):
        pass
else:
    class _ProtectionActionsMixinContract:
        pass


class ProtectionActionsMixin(_ProtectionActionsMixinContract):
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
        except PROTECTION_IQ_EXCEPTIONS as exc:
            log.warning("Protection kick failed for %s in %s: %s", nick, room, iq_error_summary(exc))
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
            db = self._require_db()
            async with db.execute(
                "SELECT id FROM redaction_index WHERE room_jid = ? AND stanza_id = ?",
                (room, stanza_id),
            ) as cursor:
                row = await cursor.fetchone()
            if row:
                await self._redaction_mark_row(int(row[0]), actor, reason)
                await db.commit()
        except Exception as exc:
            log.debug("Protection redaction mark failed for stanza %s: %s", stanza_id, exc)

    def _protection_action_cooldown_seconds(self, config: dict[str, Any]) -> int:
        """Return the short duplicate-action cooldown for message bursts."""
        return max(0, int(config.get("action_cooldown_seconds", 5) or 0))

    @staticmethod
    def _protection_action_strength(action: str) -> int:
        """Return an ordering where stronger punitive actions have larger values."""
        return protection_action_strength(action)

    def _protection_action_cooldown_entry(
        self,
        room: str,
        target: str,
        now: float,
    ) -> tuple[float, int] | None:
        """Return an active cooldown entry, dropping expired state eagerly."""
        key = (room, str(target).lower())
        entry = getattr(self, "protection_action_cooldowns", {}).get(key)
        if entry is None:
            return None
        until, _previous_strength = entry
        if until <= now:
            self.protection_action_cooldowns.pop(key, None)
            return None
        return entry

    def _protection_set_action_cooldown(
        self,
        room: str,
        target: str,
        action: str,
        seconds: int,
        now: float,
    ) -> None:
        """Set a short duplicate-action cooldown for this room/target pair."""
        if seconds <= 0:
            return
        self.protection_action_cooldowns[(room, str(target).lower())] = (
            now + seconds,
            self._protection_action_strength(action),
        )

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

    def _protection_build_match(
        self,
        *,
        protection: str,
        room: str,
        nick: str,
        msg=None,
        target_jid: str | None = None,
        action: str | None = None,
        reason: str | None = None,
        tempban_seconds: int | None = None,
        redact: bool | None = None,
        details: dict[str, Any] | None = None,
    ) -> ProtectionMatch:
        """Snapshot a matched protection before arbitration or execution."""
        config = self.protection_config(protection)
        configured_action = str(action or config.get("action", "notify")).lower().strip()
        if configured_action not in PROTECTION_ALLOWED_ACTIONS:
            configured_action = "notify"
        configured_reason = str(reason or config.get("reason") or protection).strip()
        configured_tempban = int(
            tempban_seconds or config.get("tempban_seconds", 3600) or 3600
        )
        redact_enabled = bool(config.get("redact", False) if redact is None else redact)
        jid, normalized_nick = self._protection_subject(room, nick)
        explicit_target_jid = self._protection_stable_jid(target_jid)
        target = explicit_target_jid or jid or normalized_nick
        return ProtectionMatch(
            protection=protection,
            room=room,
            nick=nick,
            target=target,
            action=configured_action,
            reason=configured_reason,
            tempban_seconds=configured_tempban,
            redact=redact_enabled,
            observe=bool(config.get("observe", False)),
            msg=msg,
            details=dict(details or {}),
        )

    def _protection_decide_match(
        self,
        match: ProtectionMatch,
        *,
        now: float | None = None,
    ) -> ProtectionDecision:
        """Arbitrate one neutral match and update cooldown state when it wins."""
        decision_time = time.time() if now is None else now
        cooldown_entry = self._protection_action_cooldown_entry(
            match.room, match.target, decision_time
        )
        decision = arbitrate_protection_match(
            match,
            cooldown_entry=cooldown_entry,
            now=decision_time,
        )
        if decision.outcome is ProtectionActionOutcome.PUNITIVE_SUPPRESSED:
            if cooldown_entry is not None:
                until, _strength = cooldown_entry
                log.debug(
                    "Protection action suppressed in %s for %s: cooldown active for %.1fs",
                    match.room,
                    match.target,
                    until - decision_time,
                )
            return decision

        if decision.outcome is ProtectionActionOutcome.PUNITIVE_EXECUTED:
            cooldown_seconds = self._protection_action_cooldown_seconds(
                self.protection_config(match.protection)
            )
            self._protection_set_action_cooldown(
                match.room,
                match.target,
                match.action,
                cooldown_seconds,
                decision_time,
            )
        return decision

    async def _protection_execute_match(
        self,
        match: ProtectionMatch,
        decision: ProtectionDecision,
    ) -> ProtectionActionOutcome:
        """Execute one already-arbitrated match and return its stable outcome."""
        if not decision.should_execute:
            return decision.outcome

        actor = f"protection:{match.protection}"
        if match.observe:
            await self.bot_send_message(
                mto=ADMIN_ROOM,
                mbody=(
                    f"👁️ {match.protection} observe match\n"
                    f"Room: {match.room}\n"
                    f"Target: {safe_jid(match.target)}\n"
                    f"Would perform: {match.action}\n"
                    f"Would redact: {'yes' if match.redact else 'no'}\n"
                    f"Reason: {match.reason}"
                ),
                mtype="groupchat",
            )
            await self._audit_protection_event(
                match.protection,
                match.room,
                match.target,
                "observe",
                match.reason,
                details={
                    "would_action": match.action,
                    "would_redact": match.redact,
                    "observe": True,
                    **match.details,
                },
            )
            return decision.outcome

        if match.redact and match.msg is not None:
            await self._protection_redact_message(match.msg, match.reason, actor)

        if match.action in {"notify", "warn"}:
            await self.bot_send_message(
                mto=ADMIN_ROOM,
                mbody=(
                    f"🛡️ {match.protection} triggered\n"
                    f"Room: {match.room}\n"
                    f"Target: {safe_jid(match.target)}\n"
                    f"Action: {match.action}\n"
                    f"Reason: {match.reason}"
                ),
                mtype="groupchat",
            )
            if match.action == "warn":
                await self.bot_send_message(
                    mto=match.room,
                    mbody=f"⚠️ {match.nick}: {match.reason}",
                    mtype="groupchat",
                )
        elif match.action == "kick":
            await self._protection_kick(match.room, match.nick, match.reason)
        elif match.action == "tempban":
            until = int(time.time()) + max(1, match.tempban_seconds)
            await self.ban_all(match.target, until, actor, match.reason, auto_redact=False)
        elif match.action == "ban":
            await self.ban_all(match.target, None, actor, match.reason, auto_redact=False)

        if match.redact and match.punitive:
            title = (
                "Auto-redaction completed after ban"
                if match.action in {"tempban", "ban"}
                else "Auto-redaction completed after protection action"
            )
            await self._protection_redact_target_messages(
                match.target,
                match.reason,
                actor,
                title=title,
            )

        await self._audit_protection_event(
            match.protection,
            match.room,
            match.target,
            match.action,
            match.reason,
            details=match.details,
        )
        return decision.outcome

    async def _protection_process_match(
        self,
        match: ProtectionMatch,
    ) -> ProtectionActionOutcome:
        """Arbitrate and execute one neutral match."""
        decision = self._protection_decide_match(match)
        return await self._protection_execute_match(match, decision)

    async def _protection_apply_action(
        self,
        *,
        protection: str,
        room: str,
        nick: str,
        msg=None,
        target_jid: str | None = None,
        action: str | None = None,
        reason: str | None = None,
        tempban_seconds: int | None = None,
        redact: bool | None = None,
        details: dict[str, Any] | None = None,
    ) -> ProtectionActionOutcome:
        """Compatibility wrapper for direct protection-action callers."""
        match = self._protection_build_match(
            protection=protection,
            room=room,
            nick=nick,
            msg=msg,
            target_jid=target_jid,
            action=action,
            reason=reason,
            tempban_seconds=tempban_seconds,
            redact=redact,
            details=details,
        )
        return await self._protection_process_match(match)

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
