"""Join and message checks for the built-in protections."""

from __future__ import annotations

import logging
import time
from typing import Any

from config import ADMIN_ROOM

from .detection import body_contains_blocked_word, count_mentions, message_looks_like_media

log = logging.getLogger(__name__)


class ProtectionChecksMixin:
    async def protection_on_join(self, room: str, nick: str, jid: str | None = None) -> None:
        """Run join-based protections for a MUC presence join."""
        if self._protection_is_exempt(room, nick, jid):
            return

        now = time.time()

        # Slixmpp emits ``got_online`` for the initial room roster right after
        # the bot joins or reconnects to a MUC.  Those presences are not real
        # new joins and must not seed first-message/new-joiner state or trigger
        # join-wave lockdowns.  Otherwise existing occupants can be treated as
        # fresh joiners after every restart.
        join_grace = max(0, int(
            self.protection_config("JoinWaveShortCircuitProtection").get("startup_grace_seconds", 30) or 0
        ))
        room_join_time = getattr(self, "room_join_time", {}).get(room)
        if room_join_time and join_grace and now - float(room_join_time) < join_grace:
            log.debug(
                "Skipping protection join hook during initial room population in %s: nick=%s",
                room,
                nick,
            )
            return

        subject = self._protection_join_subject(nick, jid)
        if self._protection_is_recent_rejoin(room, subject, now):
            log.debug(
                "Skipping protection join hook for recent rejoin in %s: nick=%s subject=%s",
                room,
                nick,
                subject,
            )
            return

        self.protection_joined_at[(room, subject)] = now
        self.protection_first_message_seen.discard((room, subject))

        protection = "JoinWaveShortCircuitProtection"
        if not self.protection_enabled(protection):
            return
        config = self.protection_config(protection)
        affiliation = str(
            getattr(self, "occupants", {}).get(room, {}).get(nick, {}).get("affiliation") or ""
        ).lower()
        if bool(config.get("ignore_member_affiliations", True)) and affiliation in {"member", "admin", "owner"}:
            log.debug(
                "Skipping %s count for member-affiliated occupant in %s: nick=%s affiliation=%s",
                protection,
                room,
                nick,
                affiliation,
            )
            return
        window = max(1, int(config.get("window_seconds", 60) or 60))
        max_joins = max(1, int(config.get("max_joins", 8) or 8))
        joins = self.protection_join_windows[room]
        joins.append(now)
        while joins and now - joins[0] > window:
            joins.popleft()
        if len(joins) >= max_joins:
            await self._protection_handle_join_wave(room, len(joins), config)

    async def _protection_handle_join_wave(self, room: str, join_count: int, config: dict[str, Any]) -> None:
        protection = "JoinWaveShortCircuitProtection"
        now = time.time()
        cooldown_seconds = max(0, int(config.get("cooldown_seconds", 60) or 0))
        existing_until = self.protection_room_lockdown_until.get(room, 0)
        if existing_until > now:
            log.debug(
                "%s suppressed in %s: cooldown active for %.1fs",
                protection,
                room,
                existing_until - now,
            )
            return
        if cooldown_seconds > 0:
            self.protection_room_lockdown_until[room] = now + cooldown_seconds

        # Reset the current wave after a trigger.  This makes repeated live
        # smoke tests deterministic and avoids one large wave causing a new
        # trigger on every follow-up join after the cooldown expires.
        self.protection_join_windows[room].clear()

        reason = str(config.get("reason") or "join wave detected")
        action = str(config.get("action") or "lockdown").lower().strip()
        notify_only = bool(config.get("notify_only", False)) or action == "notify"
        lockdown_applied = False
        if not notify_only:
            lockdown_applied = await self._protection_lockdown_room(room, config, reason)

        action_text = "notify only" if notify_only else "members-only/moderated"
        await self.bot_send_message(
            mto=ADMIN_ROOM,
            mbody=(
                f"🚨 {protection} triggered\n"
                f"Room: {room}\n"
                f"Joins in window: {join_count}\n"
                f"Action: {action_text}\n"
                f"Lockdown applied: {'yes' if lockdown_applied else 'no'}\n"
                f"Cooldown: {cooldown_seconds}s\n"
                f"Reason: {reason}"
            ),
            mtype="groupchat",
        )
        await self._audit_protection_event(
            protection,
            room,
            room,
            "notify" if notify_only else "lockdown",
            reason,
            details={"join_count": join_count},
        )

    async def protections_on_message(self, msg, room: str, nick: str, body: str) -> bool:
        """Run message-based protections. Return True when command handling should stop."""
        jid, normalized_nick = self._protection_subject(room, nick)
        if self._protection_is_exempt(room, nick, jid):
            return False
        subject = jid or normalized_nick
        now = time.time()

        # Stop after the first matching protection.  A single message should
        # never apply multiple punitive actions to the same target because that
        # can produce noisy duplicate moderation output such as a successful ban
        # followed by "Ban already exists" from a second protection path.
        if await self._protection_check_flood(msg, room, nick, subject, now):
            return True
        if await self._protection_check_first_media(msg, room, nick, subject, body, now):
            return True
        if await self._protection_check_mentions(msg, room, nick, body):
            return True
        if await self._protection_check_wordlist(msg, room, nick, subject, body, now):
            return True
        return False

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

