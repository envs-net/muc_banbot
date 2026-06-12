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
