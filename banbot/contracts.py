"""Static host contracts for BanBot's cooperative mixins.

BanBot is assembled from many small mixins.  At runtime those mixins rely on
attributes and methods supplied by sibling mixins or :class:`slixmpp.ClientXMPP`.
These Protocols document those cross-mixin dependencies without changing the
runtime MRO.  Individual mixins inherit them only while type checking.
"""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Callable
from typing import Any, Protocol

import aiosqlite

from .cache import BanTuple


class DatabaseMixinHost(Protocol):
    """Capabilities supplied to :class:`DatabaseMixin` by the composed bot."""

    db: aiosqlite.Connection | None
    protected_rooms: set[str]
    ban_cache: dict[str, BanTuple]
    ban_index_by_jid: dict[str, BanTuple]
    ban_index_by_nick: dict[str, BanTuple]
    ban_index_by_domain: dict[str, list[BanTuple]]
    bare_jid: Callable[[object | None], str | None]

    def _cache_ban(
        self,
        jid: str | None,
        nick: str | None,
        until: int,
        issuer: str | None,
        comment: str | None,
    ) -> None: ...

    def _remove_ban_from_cache(
        self,
        identifier: str,
        ban_jid: str | None = None,
        ban_nick: str | None = None,
    ) -> None: ...


class ModerationMixinHost(Protocol):
    """Capabilities supplied to :class:`ModerationMixin` by sibling mixins."""

    db: aiosqlite.Connection | None
    plugin: Any
    protected_rooms: set[str]
    occupants: dict[str, dict[str, dict[str, Any]]]
    muc_write_semaphore: asyncio.Semaphore
    ban_cache: dict[str, BanTuple]
    allow_user_cmds: bool
    show_ban_in_muc: bool
    max_tempban_days: int
    unban_check_interval: float
    bare_jid: Callable[[object | None], str | None]

    def _require_db(self) -> aiosqlite.Connection: ...

    async def bot_send_message(
        self,
        *,
        mto: str,
        mbody: str,
        mtype: str = "groupchat",
        **kwargs: Any,
    ) -> Any: ...

    def is_bot_admin_or_owner(self, room: str) -> bool: ...

    async def maybe_auto_redact_after_ban(
        self,
        jid: str,
        comment: str | None,
        *,
        actor: str | None = None,
    ) -> Any: ...

    async def find_active_jid_ban_by_nick(
        self,
        nick: str | None,
    ) -> tuple[str, int, str | None, str | None] | None: ...

    async def is_protected_admin_target(
        self,
        target: str,
        nick: str | None = None,
        jid: str | None = None,
    ) -> tuple[bool, str | None]: ...

    def is_ignored_target(
        self,
        target: str | None,
        *,
        include_domain_for_jid: bool = False,
    ) -> bool: ...

    async def upsert_ban_db(
        self,
        jid: str | None,
        nick: str | None,
        until: int,
        issuer: str | None,
        comment: str | None,
    ) -> None: ...

    async def load_bans_from_db(self) -> None: ...

    async def cleanup_old_audit_logs(self) -> int: ...

    async def rtbl_publish_ban(
        self,
        jid: str | None,
        domain: str | None,
        comment: str | None,
    ) -> None: ...

    async def rtbl_retract_ban(
        self,
        jid: str | None,
        domain: str | None,
    ) -> None: ...

    async def notify_protected(self, room: str, message: str) -> None: ...

    def log_event(self, level: int, event: str, **fields: Any) -> None: ...

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

    def _remove_domain_bans_from_cache(self, domain: str) -> None: ...

    def _remove_ban_from_cache(
        self,
        identifier: str,
        ban_jid: str | None = None,
        ban_nick: str | None = None,
    ) -> None: ...


class CommandModerationMixinHost(Protocol):
    """Capabilities required by the moderation command dispatcher."""

    db: aiosqlite.Connection | None
    command_prefix: str
    protected_rooms: set[str]
    bare_jid: Callable[[object | None], str | None]

    def _require_db(self) -> aiosqlite.Connection: ...

    async def bot_send_message(
        self,
        *,
        mto: str,
        mbody: str,
        mtype: str = "groupchat",
        **kwargs: Any,
    ) -> Any: ...

    def _actor_jid_from_room_nick(self, room: str, nick: str) -> str: ...

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

    async def unban_all(
        self,
        identifier: str,
        issuer: str | None = None,
        *,
        notify_policy: bool = True,
    ) -> bool: ...

    async def cmd_bansearch(
        self,
        query: str,
        page: int = 1,
        show_all: bool = False,
    ) -> None: ...

    async def cmd_baninfo(self, identifier: str, room: str) -> None: ...

    async def cmd_history(
        self,
        identifier: str,
        room: str,
        args: list[str] | None = None,
    ) -> None: ...

    async def _find_ban_record(
        self,
        identifier: str,
    ) -> tuple[
        int,
        str,
        str,
        str | None,
        str | None,
        int,
        str | None,
        str | None,
        int,
        int,
    ] | None: ...

    async def is_protected_admin_target(
        self,
        target: str,
        nick: str | None = None,
        jid: str | None = None,
    ) -> tuple[bool, str | None]: ...

    def is_ignored_target(
        self,
        target: str | None,
        *,
        include_domain_for_jid: bool = False,
    ) -> bool: ...

    def _remove_ban_from_cache(
        self,
        identifier: str,
        ban_jid: str | None = None,
        ban_nick: str | None = None,
    ) -> None: ...

    def _cache_ban(
        self,
        jid: str | None,
        nick: str | None,
        until: int,
        issuer: str | None,
        comment: str | None,
    ) -> None: ...

    async def apply_ban_to_room(
        self,
        room: str,
        ban_jid: str | None,
        ban_nick: str | None,
        comment: str | None,
        issuer: str | None = None,
        announce_missing_rights: bool = True,
        log_success: bool = True,
    ) -> None: ...

    async def rtbl_publish_ban(
        self,
        jid: str | None,
        domain: str | None,
        comment: str | None,
    ) -> None: ...

    def log_event(self, level: int, event: str, **fields: Any) -> None: ...

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

    async def cmd_redact(
        self,
        args: list[str],
        room: str,
        actor: str | None = None,
    ) -> None: ...

    async def sync_rooms_and_bans(self) -> None: ...

    async def sync_admins(self, announce: bool = False) -> bool: ...

    async def sync_bans(self) -> None: ...

    async def cmd_audit(self, args: list[str], room: str) -> None: ...


class SyncMixinHost(Protocol):
    """Capabilities supplied to :class:`SyncMixin` by the composed bot."""

    db: aiosqlite.Connection | None
    plugin: Any
    protected_rooms: set[str]
    occupants: dict[str, dict[str, dict[str, Any]]]
    room_join_time: dict[str, float]
    bot_admin_state: dict[str, bool]
    bare_jid: Callable[[object | None], str | None]
    safe_jid: Callable[[object], str]

    def _require_db(self) -> aiosqlite.Connection: ...

    async def bot_send_message(
        self,
        *,
        mto: str,
        mbody: str,
        mtype: str = "groupchat",
        **kwargs: Any,
    ) -> Any: ...

    def is_bot_admin_or_owner(self, room: str) -> bool: ...

    async def apply_ban_to_room(
        self,
        room: str,
        ban_jid: str | None,
        ban_nick: str | None,
        comment: str | None,
        issuer: str | None = None,
        announce_missing_rights: bool = True,
        log_success: bool = True,
    ) -> None: ...

    async def unban_all(
        self,
        identifier: str,
        issuer: str | None = None,
        *,
        notify_policy: bool = True,
    ) -> bool: ...

    async def upsert_ban_db(
        self,
        jid: str | None,
        nick: str | None,
        until: int,
        issuer: str | None,
        comment: str | None,
    ) -> None: ...

    async def maybe_auto_redact_after_manual_muc_ban(
        self,
        jid: str | None,
        comment: str | None,
        actor: str | None = None,
    ) -> None: ...


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
    protection_action_cooldowns: dict[tuple[str, str], float]

    def _require_db(self) -> aiosqlite.Connection: ...

    def is_bot_admin_or_owner(self, room: str) -> bool: ...

    def protection_config(self, name: str) -> dict[str, Any]: ...

    def _protection_subject(self, room: str, nick: str) -> tuple[str | None, str]: ...

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
    protection_join_windows: dict[str, deque[float]]
    protection_joined_at: dict[tuple[str, str], float]
    protection_first_message_seen: set[tuple[str, str]]
    protection_room_lockdown_until: dict[str, float]

    def protection_enabled(self, name: str) -> bool: ...

    def protection_config(self, name: str) -> dict[str, Any]: ...

    def _protection_join_subject(self, nick: str, jid: str | None = None) -> str: ...

    def _protection_is_recent_rejoin(self, room: str, subject: str, now: float) -> bool: ...

    def _protection_subject(self, room: str, nick: str) -> tuple[str | None, str]: ...

    def _protection_known_nicks(self, room: str) -> list[str]: ...

    def _protection_is_exempt(
        self,
        room: str,
        nick: str,
        jid: str | None = None,
    ) -> bool: ...

    async def _protection_apply_action(
        self,
        *,
        protection: str,
        room: str,
        nick: str,
        msg: Any = None,
        action: str | None = None,
        reason: str | None = None,
        tempban_seconds: int | None = None,
        redact: bool | None = None,
        details: dict[str, Any] | None = None,
    ) -> None: ...

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


class ProtectionCommandsMixinHost(Protocol):
    """Capabilities required by protection administration commands."""

    command_prefix: str
    protected_rooms: set[str]
    occupants: dict[str, dict[str, dict[str, Any]]]
    protections: dict[str, dict[str, Any]]
    protection_trusted_reports: dict[tuple[str, str], list[tuple[float, str, str]]]

    def protection_enabled(self, name: str) -> bool: ...

    def protection_config(self, name: str) -> dict[str, Any]: ...

    def _protection_actor_jid(self, room: str, nick: str) -> str | None: ...

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

    def _actor_jid_from_room_nick(self, room: str, nick: str) -> str: ...

    async def persist_protection(self, name: str) -> None: ...

    async def _protection_apply_action(
        self,
        *,
        protection: str,
        room: str,
        nick: str,
        msg: Any = None,
        action: str | None = None,
        reason: str | None = None,
        tempban_seconds: int | None = None,
        redact: bool | None = None,
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

    def init_protection_state(self) -> None: ...


class RtblRuntimeHost(Protocol):
    """Shared runtime state used across the RTBL subsystem."""

    db: aiosqlite.Connection | None
    plugin: Any
    rtbl_enabled: bool
    rtbl_announce: bool
    rtbl_subscriptions: list[tuple[str, str]]
    rtbl_hash_cache: dict[str, str | None]
    rtbl_domain_cache: dict[str, str | None]
    rtbl_last_fetch: dict[tuple[str, str], float]
    rtbl_last_change: dict[tuple[str, str], float]
    rtbl_last_error: dict[tuple[str, str], str | None]
    rtbl_last_counts: dict[tuple[str, str], tuple[int, int]]
    bare_jid: Callable[[object | None], str | None]

    def _require_db(self) -> aiosqlite.Connection: ...


class RtblDatabaseMixinHost(RtblRuntimeHost, Protocol):
    """Capabilities required by RTBL schema/cache initialization."""

    _rtbl_handlers_registered: bool

    def add_event_handler(self, name: str, handler: Callable[..., Any]) -> Any: ...

    async def _on_rtbl_publish(self, msg: Any) -> None: ...

    async def _on_rtbl_retract(self, msg: Any) -> None: ...

    async def _rtbl_subscribe_and_fetch(self, service_jid: str, node: str) -> None: ...


class RtblApplyMixinHost(RtblRuntimeHost, Protocol):
    """Capabilities required to match and apply inbound RTBL entries."""

    protected_rooms: set[str]
    occupants: dict[str, dict[str, dict[str, Any]]]

    def is_ignored_jid(self, jid: str) -> bool: ...

    def is_ignored_domain(self, domain: str) -> bool: ...

    def _rtbl_hash_jid(self, jid: str) -> str: ...

    async def bot_send_message(
        self,
        *,
        mto: str,
        mbody: str,
        mtype: str = "groupchat",
        **kwargs: Any,
    ) -> Any: ...

    async def is_protected_admin_target(
        self,
        target: str,
        nick: str | None = None,
        jid: str | None = None,
    ) -> tuple[bool, str | None]: ...

    async def upsert_ban_db(
        self,
        jid: str | None,
        nick: str | None,
        until: int,
        issuer: str | None,
        comment: str | None,
    ) -> None: ...

    async def apply_ban_to_room(
        self,
        room: str,
        ban_jid: str | None,
        ban_nick: str | None,
        comment: str | None,
        issuer: str | None = None,
        announce_missing_rights: bool = True,
        log_success: bool = True,
    ) -> None: ...

    def _remove_domain_bans_from_cache(self, domain: str) -> None: ...

    async def unban_all(
        self,
        identifier: str,
        issuer: str | None = None,
        *,
        notify_policy: bool = True,
    ) -> bool: ...

    def log_event(self, level: int, event: str, **fields: Any) -> None: ...

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


class RtblPubSubMixinHost(RtblRuntimeHost, Protocol):
    """Capabilities required by inbound RTBL PubSub handling."""

    rtbl_refresh_interval: int
    alert_on_rtbl_refresh_failures: int

    def make_iq_get(self, *, ito: str) -> Any: ...

    def _rtbl_extract_reason(self, payload: Any) -> str | None: ...

    def _rtbl_is_own_publish_node(self, service_jid: str, node: str) -> bool: ...

    async def _rebuild_rtbl_caches(self) -> None: ...

    async def _rtbl_cleanup_stale_persisted_bans(self, issuer: str = "rtbl_cleanup") -> int: ...

    async def _rtbl_check_all_occupants_against_caches(
        self,
        source: str | None = None,
    ) -> tuple[int, int]: ...

    async def _rtbl_check_all_occupants_for_hash(
        self,
        hash_val: str,
        reason: str | None,
    ) -> None: ...

    async def _rtbl_check_all_occupants_for_domain(
        self,
        domain: str,
        reason: str | None,
    ) -> None: ...

    async def bot_send_message(
        self,
        *,
        mto: str,
        mbody: str,
        mtype: str = "groupchat",
        **kwargs: Any,
    ) -> Any: ...

    async def record_alert_failure(
        self,
        key: str,
        title: str,
        message: str,
        *,
        enabled: bool = True,
        threshold: int = 1,
        details: dict[str, Any] | None = None,
    ) -> bool: ...

    def record_alert_success(self, key: str) -> None: ...


class RtblPublishMixinHost(RtblRuntimeHost, Protocol):
    """Capabilities required by BanBot's own outbound RTBL feed."""

    rtbl_publish_config_enabled: bool
    rtbl_publish_enabled: bool
    rtbl_publish_service: str
    rtbl_publish_jid_node: str
    rtbl_publish_domain_node: str
    rtbl_publish_sanity_check_ok: bool | None
    rtbl_publish_disabled_reason: str | None

    def register_plugin(self, plugin: str) -> Any: ...

    def _rtbl_hash_jid(self, jid: str) -> str: ...

    def _rtbl_build_payload(self, comment: str | None) -> Any: ...

    async def bot_send_message(
        self,
        *,
        mto: str,
        mbody: str,
        mtype: str = "groupchat",
        **kwargs: Any,
    ) -> Any: ...
