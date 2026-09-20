"""Static host contracts for BanBot's protection pipeline."""

from __future__ import annotations

from collections import deque
from typing import TYPE_CHECKING, Any, Protocol

import aiosqlite

from .common import ActorJidResolverHost

if TYPE_CHECKING:
    from ..protections.decision import ProtectionMatch
    from ..protections.definitions import ProtectionActionOutcome



class ProtectionCoordinatorHost(Protocol):
    """External capabilities used by the protection subsystem coordinator."""

    command_prefix: str
    plugin: Any
    protected_rooms: set[str]
    occupants: dict[str, dict[str, dict[str, Any]]]

    def is_admin_or_owner(
        self,
        room: str,
        nick: str | None = None,
        jid: str | None = None,
    ) -> bool: ...



class ProtectionActionsMixinHost(Protocol):
    """Capabilities required to execute protection actions."""

    db: aiosqlite.Connection | None
    plugin: Any
    muc_write_semaphore: Any
    redaction_enabled: bool
    protection_action_cooldowns: dict[tuple[str, str], tuple[float, int]]

    def _require_db(self) -> aiosqlite.Connection: ...

    def is_bot_admin_or_owner(self, room: str, *, log_missing: bool = True) -> bool: ...

    def protection_config(self, name: str) -> dict[str, Any]: ...

    def _protection_subject(self, room: str, nick: str) -> tuple[str | None, str]: ...

    def _protection_stable_jid(self, jid: str | None) -> str | None: ...

    async def bot_send_message(
        self,
        *,
        mto: str,
        mbody: str,
        mtype: str = "groupchat",
        **kwargs: Any,
    ) -> Any: ...

    async def ban_all(
        self,
        identifier: str,
        until: int | None,
        issuer: str,
        comment: str | None = None,
        *,
        auto_redact: bool = True,
        notify_policy: bool = True,
    ) -> None: ...

    def _redaction_extract_stanza_id(self, msg: Any) -> str | None: ...

    async def _redaction_send_retract(
        self,
        room_jid: str,
        stanza_id: str,
        reason: str | None,
    ) -> None: ...

    async def flush_redaction_index(self) -> None: ...

    async def _redaction_mark_row(
        self,
        row_id: int,
        actor: str | None,
        reason: str | None,
    ) -> None: ...

    async def audit_event(
        self,
        event_type: str,
        actor: str | None = None,
        room: str | None = None,
        target_type: str | None = None,
        target: str | None = None,
        jid: str | None = None,
        nick: str | None = None,
        until: int | None = None,
        comment: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None: ...



class ProtectionChecksMixinHost(ProtectionCoordinatorHost, Protocol):
    """Runtime state and helpers required by protection detectors."""

    room_join_time: dict[str, float]
    occupants: dict[str, dict[str, dict[str, Any]]]
    protection_message_windows: dict[tuple[str, str, str], deque[float]]
    protection_similar_messages: dict[str, deque[tuple[float, str, str]]]
    protection_join_windows: dict[str, deque[tuple[float, str]]]
    protection_joined_at: dict[tuple[str, str], float]
    protection_first_message_seen: set[tuple[str, str]]
    protection_established_at_join: set[tuple[str, str]]
    protection_known_participants: set[tuple[str, str]]
    protection_persisted_known_participants: set[tuple[str, str]]
    protection_room_lockdown_until: dict[str, float]

    def protection_enabled(self, name: str) -> bool: ...

    def protection_config(self, name: str) -> dict[str, Any]: ...

    def _protection_stable_jid(self, jid: str | None) -> str | None: ...

    def _protection_join_subject(self, nick: str, jid: str | None = None) -> str: ...

    def _protection_is_recent_rejoin(self, room: str, subject: str, now: float) -> bool: ...

    def _protection_participant_is_known(self, room: str, subject: str) -> bool: ...

    def _protection_mark_participant_known(self, room: str, subject: str) -> bool: ...

    def _protection_subject(self, room: str, nick: str) -> tuple[str | None, str]: ...

    def _protection_known_nicks(self, room: str) -> list[str]: ...

    async def remember_protection_participant(
        self,
        room: str,
        subject: str,
        *,
        persistent: bool,
    ) -> None: ...

    def _protection_is_exempt(
        self,
        room: str,
        nick: str,
        jid: str | None = None,
    ) -> bool: ...

    def _protection_build_match(
        self,
        *,
        protection: str,
        room: str,
        nick: str,
        msg: Any = None,
        target_jid: str | None = None,
        action: str | None = None,
        reason: str | None = None,
        tempban_seconds: int | None = None,
        redact: bool | None = None,
        details: dict[str, Any] | None = None,
    ) -> ProtectionMatch: ...

    async def _protection_process_match(
        self,
        match: ProtectionMatch,
    ) -> ProtectionActionOutcome: ...

    async def _protection_apply_action(
        self,
        *,
        protection: str,
        room: str,
        nick: str,
        msg: Any = None,
        target_jid: str | None = None,
        action: str | None = None,
        reason: str | None = None,
        tempban_seconds: int | None = None,
        redact: bool | None = None,
        details: dict[str, Any] | None = None,
    ) -> ProtectionActionOutcome: ...

    async def _protection_lockdown_room(
        self,
        room: str,
        config: dict[str, Any],
        reason: str,
    ) -> bool: ...

    async def _audit_protection_event(
        self,
        protection: str,
        room: str,
        target: str,
        action: str,
        reason: str,
        details: dict[str, Any] | None = None,
    ) -> None: ...

    async def bot_send_message(
        self,
        *,
        mto: str,
        mbody: str,
        mtype: str = "groupchat",
        **kwargs: Any,
    ) -> Any: ...



class ProtectionCommandsMixinHost(ActorJidResolverHost, Protocol):
    """Capabilities required by protection administration commands."""

    command_prefix: str
    protected_rooms: set[str]
    occupants: dict[str, dict[str, dict[str, Any]]]
    protections: dict[str, dict[str, Any]]
    protection_trusted_reports: dict[tuple[str, str], list[tuple[float, str, str]]]

    def protection_enabled(self, name: str) -> bool: ...

    def protection_config(self, name: str) -> dict[str, Any]: ...

    def _protection_actor_jid(self, room: str, nick: str) -> str | None: ...

    def _protection_stable_jid(self, jid: str | None) -> str | None: ...

    def _protection_is_exempt(
        self,
        room: str,
        nick: str,
        jid: str | None = None,
    ) -> bool: ...

    def _resolve_protection_or_error(
        self,
        name: str,
    ) -> tuple[str, None] | tuple[None, str]: ...

    async def persist_protection(self, name: str) -> None: ...

    async def _protection_apply_action(
        self,
        *,
        protection: str,
        room: str,
        nick: str,
        msg: Any = None,
        target_jid: str | None = None,
        action: str | None = None,
        reason: str | None = None,
        tempban_seconds: int | None = None,
        redact: bool | None = None,
        details: dict[str, Any] | None = None,
    ) -> ProtectionActionOutcome: ...

    async def bot_send_message(
        self,
        *,
        mto: str,
        mbody: str,
        mtype: str = "groupchat",
        **kwargs: Any,
    ) -> Any: ...

    async def audit_event(
        self,
        event_type: str,
        actor: str | None = None,
        room: str | None = None,
        target_type: str | None = None,
        target: str | None = None,
        jid: str | None = None,
        nick: str | None = None,
        until: int | None = None,
        comment: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None: ...



class ProtectionNotificationMixinHost(Protocol):
    """Capabilities required for protection policy notifications."""

    def protection_enabled(self, name: str) -> bool: ...

    def protection_config(self, name: str) -> dict[str, Any]: ...

    async def bot_send_message(
        self,
        *,
        mto: str,
        mbody: str,
        mtype: str = "groupchat",
        **kwargs: Any,
    ) -> Any: ...



class ProtectionStorageMixinHost(Protocol):
    """State required to persist protection configuration."""

    db: aiosqlite.Connection | None
    protections: dict[str, dict[str, Any]]
    protection_known_participants: set[tuple[str, str]]
    protection_persisted_known_participants: set[tuple[str, str]]
    _protection_storage_ready: bool

    def init_protection_state(self) -> None: ...

    def _protection_participant_key(self, room: str, subject: str) -> tuple[str, str]: ...

    def _protection_mark_participant_known(self, room: str, subject: str) -> bool: ...
