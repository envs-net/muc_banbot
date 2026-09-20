"""Join and message checks for the built-in protections."""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any

from config import ADMIN_ROOM

from .detection import (
    body_contains_blocked_word,
    count_mentions,
    message_body_without_reply_fallback,
    message_looks_like_media,
    messages_are_similar,
    normalize_spam_body,
    normalized_word_count,
)

log = logging.getLogger(__name__)


if TYPE_CHECKING:
    from ..contracts import ProtectionChecksMixinHost

    class _ProtectionChecksMixinContract(ProtectionChecksMixinHost):
        pass
else:
    class _ProtectionChecksMixinContract:
        pass


class ProtectionChecksMixin(_ProtectionChecksMixinContract):
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
            stable_jid = self._protection_stable_jid(jid)
            if stable_jid:
                self._protection_mark_participant_known(room, stable_jid)
            log.debug(
                "Skipping protection join hook during initial room population in %s: nick=%s",
                room,
                nick,
            )
            return

        subject = self._protection_join_subject(nick, jid)
        stable_jid = self._protection_stable_jid(jid)
        known_before_join = bool(
            stable_jid and self._protection_participant_is_known(room, stable_jid)
        )
        join_key = (room, subject)
        if known_before_join:
            self.protection_established_at_join.add(join_key)
        else:
            self.protection_established_at_join.discard(join_key)

        if self._protection_is_recent_rejoin(room, subject, now):
            log.debug(
                "Skipping protection join hook for recent rejoin in %s: nick=%s subject=%s",
                room,
                nick,
                subject,
            )
            return

        self.protection_joined_at[join_key] = now
        self.protection_first_message_seen.discard(join_key)

        protection = "JoinWaveShortCircuitProtection"
        if not self.protection_enabled(protection):
            return
        config = self.protection_config(protection)
        if known_before_join and bool(config.get("ignore_known_participants", True)):
            log.debug(
                "Skipping %s count for established participant in %s: nick=%s subject=%s",
                protection,
                room,
                nick,
                subject,
            )
            return
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
        while joins and now - joins[0][0] > window:
            joins.popleft()
        if any(existing_subject == subject for _seen_at, existing_subject in joins):
            log.debug(
                "Skipping duplicate %s subject within active window in %s: %s",
                protection,
                room,
                subject,
            )
            return
        joins.append((now, subject))
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
        observe = bool(config.get("observe", False))
        notify_only = bool(config.get("notify_only", False)) or action == "notify" or observe
        lockdown_applied = False
        if not notify_only:
            lockdown_applied = await self._protection_lockdown_room(room, config, reason)

        action_text = "observe (would lock down)" if observe else ("notify only" if notify_only else "members-only/moderated")
        await self.bot_send_message(
            mto=ADMIN_ROOM,
            mbody=(
                f"🚨 {protection} triggered\n"
                f"Room: {room}\n"
                f"Unique joins in window: {join_count}\n"
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
            "observe" if observe else ("notify" if notify_only else "lockdown"),
            reason,
            details={"join_count": join_count, "would_action": action if observe else None, "observe": observe},
        )

    async def protections_on_message(self, msg, room: str, nick: str, body: str) -> bool:
        """Run message-based protections. Return True when command handling should stop."""
        jid, normalized_nick = self._protection_subject(room, nick)
        if self._protection_is_exempt(room, nick, jid):
            return False
        subject = jid or normalized_nick
        now = time.time()
        protection_body = message_body_without_reply_fallback(msg, body)
        if protection_body != body:
            log.debug(
                "Ignoring XEP-0461 reply fallback while evaluating protections in %s: nick=%s",
                room,
                nick,
            )

        # Stop after the first *enforcing* protection. Observe-only matches are
        # allowed to fall through so an observing rule cannot hide a later real
        # enforcement rule for the same message.
        if await self._protection_check_flood(msg, room, nick, subject, now):
            return True
        first_media_stop, remember_participant = await self._protection_check_first_media(
            msg,
            room,
            nick,
            subject,
            protection_body,
            now,
        )
        if first_media_stop:
            return True
        if await self._protection_check_similar_messages(msg, room, nick, subject, protection_body, now):
            return True
        mention_stop, mention_matched = await self._protection_check_mentions(
            msg, room, nick, protection_body
        )
        if mention_stop:
            return True
        wordlist_stop, wordlist_matched = await self._protection_check_wordlist(
            msg, room, nick, subject, protection_body, now
        )
        if wordlist_stop:
            return True
        if remember_participant and not mention_matched and not wordlist_matched:
            await self.remember_protection_participant(
                room,
                subject,
                persistent=jid is not None,
            )
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
        return not bool(config.get("observe", False))

    async def _protection_check_first_media(
        self,
        msg,
        room: str,
        nick: str,
        subject: str,
        body: str,
        now: float,
    ) -> tuple[bool, bool]:
        """Return ``(stop_processing, remember_participant)`` for first-media checks."""
        protection = "FirstMessageMediaProtection"
        key = (room, subject)
        already_seen = key in self.protection_first_message_seen
        if not already_seen:
            self.protection_first_message_seen.add(key)
        if self._protection_participant_is_known(room, subject):
            return False, not already_seen
        if not self.protection_enabled(protection):
            return False, not already_seen
        if already_seen:
            return False, False
        joined_at = self.protection_joined_at.get(key)
        if joined_at is None:
            return False, True
        config = self.protection_config(protection)
        grace = max(0, int(config.get("join_grace_seconds", 600) or 0))
        if grace and now - joined_at > grace:
            return False, True
        if not message_looks_like_media(body):
            return False, True
        await self._protection_apply_action(
            protection=protection,
            room=room,
            nick=nick,
            msg=msg,
            details={"first_message": True},
        )
        return not bool(config.get("observe", False)), False


    async def _protection_check_similar_messages(
        self,
        msg,
        room: str,
        nick: str,
        subject: str,
        body: str,
        now: float,
    ) -> bool:
        protection = "SimilarMessageProtection"
        if not self.protection_enabled(protection):
            return False
        config = self.protection_config(protection)
        normalized = normalize_spam_body(body)
        min_length = max(1, int(config.get("min_length", 20) or 20))
        min_words = max(1, int(config.get("min_words", 3) or 3))
        if len(normalized) < min_length or normalized_word_count(normalized) < min_words:
            return False

        window = max(1, int(config.get("window_seconds", 120) or 120))
        max_similar = max(2, int(config.get("max_similar", 3) or 3))
        similarity_percent = max(1, min(100, int(config.get("similarity_percent", 90) or 90)))
        entries = self.protection_similar_messages[room]
        entries.append((now, subject, normalized))
        while entries and now - entries[0][0] > window:
            entries.popleft()

        similar_entries = [
            entry for entry in entries
            if messages_are_similar(normalized, entry[2], similarity_percent=similarity_percent)
        ]
        if len(similar_entries) < max_similar:
            return False

        await self._protection_apply_action(
            protection=protection,
            room=room,
            nick=nick,
            msg=msg,
            details={
                "similar_messages": len(similar_entries),
                "window_seconds": window,
                "similarity_percent": similarity_percent,
            },
        )
        entries.clear()
        return not bool(config.get("observe", False))

    async def _protection_check_mentions(
        self, msg, room: str, nick: str, body: str
    ) -> tuple[bool, bool]:
        protection = "MentionLimitProtection"
        if not self.protection_enabled(protection):
            return False, False
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
            return False, False
        await self._protection_apply_action(
            protection=protection,
            room=room,
            nick=nick,
            msg=msg,
            details={"mention_count": mention_count, "max_mentions": limit},
        )
        return not bool(config.get("observe", False)), True

    async def _protection_check_wordlist(
        self, msg, room: str, nick: str, subject: str, body: str, now: float
    ) -> tuple[bool, bool]:
        protection = "WordListNewJoinerProtection"
        if not self.protection_enabled(protection):
            return False, False
        config = self.protection_config(protection)
        words = list(config.get("words", []) or [])
        if not words:
            return False, False
        key = (room, subject)
        if key in self.protection_established_at_join:
            return False, False
        joined_at = self.protection_joined_at.get(key)
        if joined_at is None:
            return False, False
        grace = max(0, int(config.get("join_grace_seconds", 900) or 0))
        if grace and now - joined_at > grace:
            return False, False
        word = body_contains_blocked_word(body, words)
        if not word:
            return False, False
        reason = f"{config.get('reason') or 'blocked word from new joiner'}: {word}"
        await self._protection_apply_action(
            protection=protection,
            room=room,
            nick=nick,
            msg=msg,
            reason=reason,
            details={"word": word},
        )
        return not bool(config.get("observe", False)), True
