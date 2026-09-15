"""Static host contracts for BanBot's cooperative mixins.

BanBot is assembled from many small mixins.  At runtime those mixins rely on
attributes and methods supplied by sibling mixins or :class:`slixmpp.ClientXMPP`.
These Protocols document those cross-mixin dependencies without changing the
runtime MRO.  Individual mixins inherit them only while type checking.
"""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Awaitable, Callable, Iterable
from pathlib import Path
from typing import Any, Protocol

import aiosqlite
from envs_xmpp_core.release.state import ReleaseState
from slixmpp import JID

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

    def is_bot_admin_or_owner(self, room: str, *, log_missing: bool = True) -> bool: ...

    async def maybe_auto_redact_after_ban(
        self,
        jid: str | None,
        comment: str | None,
        actor: str | None = None,
    ) -> None: ...

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

    def is_bot_admin_or_owner(self, room: str, *, log_missing: bool = True) -> bool: ...

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

    def is_bot_admin_or_owner(self, room: str, *, log_missing: bool = True) -> bool: ...

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


class CommandRtblMixinHost(Protocol):
    """Capabilities required by the top-level RTBL command dispatcher."""

    rtbl_enabled: bool

    async def bot_send_message(
        self,
        *,
        mto: str,
        mbody: str,
        mtype: str = "groupchat",
        **kwargs: Any,
    ) -> Any: ...

    def _actor_jid_from_room_nick(self, room: str, nick: str) -> str: ...

    async def cmd_rtbl(
        self,
        args: list[str],
        room: str,
        actor: str = "unknown",
    ) -> None: ...


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


class RtblCommandMixinHost(RtblRuntimeHost, Protocol):
    """Capabilities required by RTBL administration commands."""

    command_prefix: str
    rtbl_publish_enabled: bool
    rtbl_publish_service: str
    rtbl_publish_jid_node: str
    rtbl_publish_domain_node: str

    async def bot_send_message(
        self,
        *,
        mto: str,
        mbody: str,
        mtype: str = "groupchat",
        **kwargs: Any,
    ) -> Any: ...

    def _rtbl_is_own_publish_node(self, service_jid: str, node: str) -> bool: ...

    async def _rtbl_subscribe_node(
        self,
        service_jid: str,
        node: str,
    ) -> tuple[bool, str | None]: ...

    async def _load_rtbl_subscriptions_from_db(self) -> None: ...

    async def _rtbl_fetch_all_items(
        self,
        service_jid: str,
        node: str,
        scan_occupants: bool = True,
    ) -> bool: ...

    async def _rtbl_cleanup_stale_persisted_bans(
        self,
        issuer: str = "rtbl_cleanup",
    ) -> int: ...

    async def _rtbl_count_active_publish_bans(self) -> tuple[int, int]: ...

    async def _rtbl_sync_all_bans_to_nodes(self) -> tuple[int, int, int, int]: ...

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


class CommandRoomsMixinHost(Protocol):
    """Capabilities required by the top-level room command dispatcher."""

    async def bot_send_message(
        self,
        *,
        mto: str,
        mbody: str,
        mtype: str = "groupchat",
        **kwargs: Any,
    ) -> Any: ...

    def _room_usage_text(self) -> str: ...

    async def cmd_room(self, args: list[str], room: str) -> None: ...


class BotOccupantMixinHost(Protocol):
    """Runtime identity state used by the canonical bot-occupant lookup."""

    occupants: dict[str, dict[str, dict[str, Any]]]
    room_bot_nicks: dict[str, str]
    boundjid: Any
    bare_jid: Callable[[object | None], str | None]


class ProtectedRoomMixinHost(Protocol):
    """Capabilities required by protected-room persistence and commands."""

    db: aiosqlite.Connection | None
    plugin: Any
    command_prefix: str
    protected_rooms: set[str]
    registered_rooms: set[str]
    occupants: dict[str, dict[str, dict[str, Any]]]
    bot_admin_state: dict[str, bool]

    def _require_db(self) -> aiosqlite.Connection: ...

    def _bot_occupant_entry(
        self,
        room: str,
    ) -> tuple[str | None, dict[str, Any] | None]: ...

    def add_event_handler(self, name: str, handler: Callable[..., Any]) -> Any: ...

    async def muc_online(self, presence: Any) -> None: ...

    async def muc_offline(self, presence: Any) -> None: ...

    async def ensure_muc_joined(
        self,
        room: str,
        *,
        nick: str = ...,
        timeout: float | None = None,
        retries: int | None = None,
        force: bool = False,
    ) -> bool: ...

    async def sync_bans_to_rooms_for_single_room(self, room: str) -> Any: ...

    async def check_jid_against_rtbl(self, jid: str, nick: str) -> bool: ...

    async def cmd_room_invite(self, args: list[str], room: str) -> None: ...

    async def bot_send_message(
        self,
        *,
        mto: str,
        mbody: str,
        mtype: str = "groupchat",
        **kwargs: Any,
    ) -> Any: ...


class RoomInviteMixinHost(Protocol):
    """Capabilities required by protected-room invite persistence and commands."""

    db: aiosqlite.Connection | None
    command_prefix: str
    protected_rooms: set[str]
    bare_jid: Callable[[object | None], str | None]

    async def validate_room_jid(self, room_jid: str) -> tuple[bool, str]: ...

    async def cmd_room(self, args: list[str], room: str) -> None: ...

    async def bot_send_message(
        self,
        *,
        mto: str,
        mbody: str,
        mtype: str = "groupchat",
        **kwargs: Any,
    ) -> Any: ...


class MucMixinHost(Protocol):
    """Cross-subsystem state and helpers required by the MUC lifecycle mixin."""

    db: aiosqlite.Connection | None
    plugin: Any
    boundjid: Any
    protected_rooms: set[str]
    occupants: dict[str, dict[str, dict[str, Any]]]
    bot_admin_state: dict[str, bool]
    room_join_time: dict[str, float]
    room_bot_nicks: dict[str, str]
    room_join_events: dict[str, asyncio.Event]
    ban_index_by_jid: dict[str, BanTuple]
    ban_index_by_nick: dict[str, BanTuple]
    ban_index_by_domain: dict[str, list[BanTuple]]
    show_ban_in_muc: bool
    reconnecting: bool
    bare_jid: Callable[[object | None], str | None]

    def _require_db(self) -> aiosqlite.Connection: ...

    def connect(self, *args: Any, **kwargs: Any) -> Any: ...

    def disconnect(self, *args: Any, **kwargs: Any) -> Any: ...

    def is_admin_or_owner(
        self,
        room: str,
        nick: str | None = None,
        jid: str | None = None,
    ) -> bool: ...

    async def check_jid_against_rtbl(self, jid: str, nick: str) -> bool: ...

    async def upsert_ban_db(
        self,
        jid: str | None,
        nick: str | None,
        until: int,
        issuer: str | None,
        comment: str | None,
    ) -> None: ...

    async def load_bans_from_db(self) -> None: ...

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

    async def verify_admin_rights(self, room: str) -> bool: ...

    async def bot_send_message(
        self,
        *,
        mto: str,
        mbody: str,
        mtype: str = "groupchat",
        **kwargs: Any,
    ) -> Any: ...


class AdminMixinHost(BotOccupantMixinHost, Protocol):
    """Cross-subsystem state required by admin/owner authorization helpers."""

    plugin: Any
    protected_rooms: set[str]
    admin_affiliation_query_forbidden_rooms: set[str]
    _admin_affiliation_cache_entries: dict[str, tuple[float, frozenset[str]]]
    bot_admin_state: dict[str, bool]

    async def bot_send_message(
        self,
        *,
        mto: str,
        mbody: str,
        mtype: str = "groupchat",
        **kwargs: Any,
    ) -> Any: ...


class CommandEntryPointMixinHost(Protocol):
    """State and routing hooks required by the groupchat command entry point."""

    command_prefix: str
    protected_rooms: set[str]
    allow_user_cmds: bool
    occupants: dict[str, dict[str, dict[str, Any]]]

    def _set_reply_encryption_context(self, encrypted: bool | None) -> Any: ...

    def _reset_reply_encryption_context(self, token: Any) -> None: ...

    async def _handle_user_command(
        self,
        msg: Any,
        room: str,
        nick: str,
        cmd: str,
        args: list[str],
    ) -> bool: ...

    async def _handle_admin_command(
        self,
        msg: Any,
        room: str,
        nick: str,
        cmd: str,
        args: list[str],
    ) -> bool: ...

    async def _handle_unknown_command(self, msg: Any, room: str, cmd: str) -> None: ...


class CommandRouterMixinHost(Protocol):
    """Capabilities required by public/admin command routing."""

    command_prefix: str
    public_command_rate_limit_window: int
    public_command_rate_limit_max: int
    public_command_rate_limit_hits: dict[tuple[str, str, str], list[float]]

    def is_authorized(self, msg: Any) -> bool: ...

    def user_cmds_allowed(self, room: str) -> bool: ...

    async def bot_send_message(
        self,
        *,
        mto: str,
        mbody: str,
        mtype: str = "groupchat",
        **kwargs: Any,
    ) -> Any: ...

    def _admin_help_response(self, args: list[str]) -> str: ...

    async def _user_help_text(self) -> str: ...

    async def cmd_banlist_rtbl(
        self,
        room: str,
        page: int = 1,
        show_all: bool = False,
    ) -> None: ...

    async def cmd_banlist(
        self,
        room: str,
        page: int = 1,
        show_all: bool = False,
    ) -> None: ...

    async def cmd_why(self, identifier: str, room: str) -> None: ...

    async def _cmd_whoami(self, room: str, nick: str) -> None: ...

    async def cmd_protection_report(
        self,
        room: str,
        nick: str,
        args: list[str],
    ) -> None: ...

    async def _cmd_public_policy_show(self, room: str) -> None: ...

    async def _dispatch_runtime_admin_command(
        self,
        room: str,
        nick: str,
        cmd: str,
        args: list[str],
    ) -> bool: ...


class CommandRuntimeMixinHost(Protocol):
    """Runtime command and restart lifecycle hooks required by command handlers."""

    command_prefix: str
    version_check_url: str | None
    tasks: Any
    runtime_watchdog: Any
    _restart_task: asyncio.Task[Any] | None
    _restart_schedule_lock: asyncio.Lock
    _shutdown_in_progress: bool
    _shutdown_complete: bool

    def _actor_jid_from_room_nick(self, room: str, nick: str) -> str | None: ...

    async def _cmd_config(
        self,
        room: str,
        args: list[str] | None = None,
        actor: str | None = None,
    ) -> None: ...

    async def _cmd_reloadconfig(self, room: str) -> None: ...

    async def _cmd_status(self, room: str, args: list[str] | None = None) -> None: ...

    def _tasks_usage_text(self) -> str: ...

    async def bot_send_message(
        self,
        *,
        mto: str,
        mbody: str,
        mtype: str = "groupchat",
        **kwargs: Any,
    ) -> Any: ...

    async def check_for_updates_once(
        self,
        announce: bool = True,
    ) -> tuple[bool, str | None, str | None]: ...

    async def shutdown(self) -> None: ...

    async def flush_redaction_index(self) -> None: ...

    async def stop_background_tasks(self) -> None: ...

    def disconnect(self, *args: Any, **kwargs: Any) -> Any: ...

    def _schedule_restart_task(
        self,
        operation: Callable[[], Awaitable[None]],
        *,
        name: str,
    ) -> bool: ...

    def _restart_task_pending(self) -> bool: ...


class MessagingMixinHost(Protocol):
    """Transport hooks required by the centralized outbound messaging layer."""

    omemo_plaintext_fallback: bool

    def send_message(self, **kwargs: Any) -> Any: ...

    async def _send_omemo_message(
        self,
        *,
        mto: str,
        mbody: str,
        mtype: str = "groupchat",
        **kwargs: Any,
    ) -> Any: ...


class DirectMessageMixinHost(Protocol):
    """State and command hooks required by the DM/MUC-PM entry point."""

    boundjid: Any
    command_prefix: str
    allow_admin_commands_in_dms: bool
    protected_rooms: set[str]
    occupants: dict[str, dict[str, dict[str, Any]]]
    version_check_url: str | None
    bare_jid: Callable[[object | None], str | None]

    async def bot_send_message(
        self,
        *,
        mto: str,
        mbody: str,
        mtype: str = "groupchat",
        **kwargs: Any,
    ) -> Any: ...

    def _set_reply_target_context(self, mto: str, mtype: str) -> Any: ...

    def _reset_reply_target_context(self, token: Any) -> None: ...

    def _set_reply_encryption_context(self, encrypted: bool | None) -> Any: ...

    def _reset_reply_encryption_context(self, token: Any) -> None: ...

    def _admin_help_response(self, args: list[str]) -> str: ...

    async def _cmd_config(
        self,
        room: str,
        args: list[str] | None = None,
        actor: str | None = None,
    ) -> None: ...

    async def _cmd_status(self, room: str, args: list[str] | None = None) -> None: ...

    async def _cmd_tasks(
        self,
        room: str,
        args: list[str],
        *,
        mtype: str = "groupchat",
    ) -> None: ...

    async def check_for_updates_once(
        self,
        announce: bool = True,
    ) -> tuple[bool, str | None, str | None]: ...

    async def cmd_protections_list(self, room: str, args: list[str]) -> None: ...

    async def cmd_omemo(
        self,
        args: list[str],
        room: str,
        actor: str | None = None,
    ) -> None: ...

    async def cmd_banlist_rtbl(
        self,
        room: str,
        page: int = 1,
        show_all: bool = False,
    ) -> None: ...

    async def cmd_banlist(
        self,
        room: str,
        page: int = 1,
        show_all: bool = False,
    ) -> None: ...

    async def cmd_room(self, args: list[str], room: str) -> None: ...

    async def cmd_ignore(
        self,
        args: list[str],
        room: str,
        actor: str = "unknown",
        command_name: str = "ignore",
    ) -> None: ...

    async def cmd_rtbl(
        self,
        args: list[str],
        room: str,
        actor: str = "unknown",
    ) -> None: ...

    async def cmd_audit(self, args: list[str], room: str) -> None: ...

    async def cmd_baninfo(self, identifier: str, room: str) -> None: ...

    async def cmd_history(
        self,
        identifier: str,
        room: str,
        args: list[str] | None = None,
    ) -> None: ...

    async def cmd_why(self, identifier: str, room: str) -> None: ...

    async def cmd_bansearch(
        self,
        query: str,
        page: int = 1,
        show_all: bool = False,
    ) -> None: ...


class OmemoCoreMixinHost(Protocol):
    """Slixmpp/runtime capabilities required by the OMEMO core helpers."""

    plugin: Any
    boundjid: Any
    occupants: dict[str, dict[str, dict[str, Any]]]
    omemo_enabled: bool
    omemo_storage_file: str
    omemo_auto_encrypt_admin_room: bool
    omemo_plaintext_fallback: bool
    omemo_reset_on_identity_change: bool
    omemo_reset_pending_restart: bool
    omemo_ready_timeout: int
    omemo_ready: asyncio.Event

    def register_plugin(
        self,
        plugin: str,
        config: dict[str, Any] | None = None,
        module: Any = None,
    ) -> Any: ...

    def add_event_handler(
        self,
        name: str,
        handler: Callable[..., Any],
        *args: Any,
        **kwargs: Any,
    ) -> Any: ...

    def make_message(
        self,
        *,
        mto: str,
        mbody: str,
        mtype: str,
        **kwargs: Any,
    ) -> Any: ...


class OmemoDeviceMixinHost(Protocol):
    """Capabilities required by OMEMO device diagnostics."""

    omemo_enabled: bool
    omemo_storage_file: str

    async def _omemo_recipients_for_room(self, room_jid: str) -> set[JID]: ...

    async def bot_send_message(
        self,
        *,
        mto: str,
        mbody: str,
        mtype: str = "groupchat",
        **kwargs: Any,
    ) -> Any: ...


class OmemoResetMixinHost(Protocol):
    """State and command hooks required by OMEMO reset handling."""

    command_prefix: str
    omemo_storage_file: str
    omemo_enabled: bool
    omemo_reset_pending_restart: bool
    omemo_ready: asyncio.Event

    def _schedule_restart_task(
        self,
        operation: Callable[[], Awaitable[None]],
        *,
        name: str,
    ) -> bool: ...

    def _restart_task_pending(self) -> bool: ...

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

    async def _cmd_omemo_status(self, room: str) -> None: ...

    async def _cmd_omemo_devices(self, room: str) -> None: ...


class OmemoStatusMixinHost(Protocol):
    """State and output hook required by OMEMO status rendering."""

    omemo_storage_file: str
    omemo_ready: asyncio.Event

    async def bot_send_message(
        self,
        *,
        mto: str,
        mbody: str,
        mtype: str = "groupchat",
        **kwargs: Any,
    ) -> Any: ...


class CommandOmemoMixinHost(Protocol):
    """Capabilities required by the top-level ``!omemo`` dispatcher."""

    def _actor_jid_from_room_nick(self, room: str, nick: str) -> str: ...

    async def cmd_omemo(
        self,
        args: list[str],
        room: str,
        actor: str | None = None,
    ) -> None: ...


class HealthCheckMixinHost(Protocol):
    """Runtime state and recovery hooks required by periodic health checks."""

    protected_rooms: set[str]
    occupants: dict[str, dict[str, dict[str, Any]]]
    bot_admin_state: dict[str, bool]
    reconnecting: bool
    reconnect_task: asyncio.Task[Any] | None
    last_audit_cleanup_run: float
    health_check_interval: float

    async def bot_send_message(
        self,
        *,
        mto: str,
        mbody: str,
        mtype: str = "groupchat",
        **kwargs: Any,
    ) -> Any: ...

    async def send_operational_alert(
        self,
        key: str,
        title: str,
        message: str,
        *,
        enabled: bool = True,
        details: dict[str, Any] | None = None,
    ) -> bool: ...

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

    async def ensure_muc_joined(
        self,
        room: str,
        *,
        force: bool = False,
        retries: int | None = None,
    ) -> bool: ...

    async def sync_bans_to_rooms_for_single_room(self, room: str) -> None: ...

    def is_bot_admin_or_owner(
        self,
        room: str,
        *,
        log_missing: bool = True,
    ) -> bool: ...

    async def cleanup_old_audit_logs(self) -> int: ...

    async def get_db_stats(self) -> dict[str, Any]: ...


class OutboxMixinHost(Protocol):
    """Outbound transport hook required by the durable outbox worker."""

    tasks: Any

    async def _send_message_transport(
        self,
        *,
        mto: str,
        mbody: str,
        mtype: str,
        encrypted: bool | None,
        raise_on_failure: bool = False,
        **kwargs: Any,
    ) -> Any: ...


class ReleaseStateHost(Protocol):
    """Database lifecycle state required by release-state persistence."""

    db: aiosqlite.Connection | None


class UpdateMixinHost(ReleaseStateHost, Protocol):
    """Process/version state required by update checks and startup notices."""

    _startup_release_state: ReleaseState | None
    version_check_enabled: bool
    version_check_interval: float
    version_check_url: str | None
    last_version_check_result: str | None
    last_update_notified_version: str | None
    previous_startup_version: str | None
    announce_startup: bool

    async def bot_send_message(
        self,
        *,
        mto: str,
        mbody: str,
        mtype: str = "groupchat",
        **kwargs: Any,
    ) -> Any: ...


class AlertMixinHost(Protocol):
    """Messaging/audit hooks required by operational alert delivery."""

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


class StatusHealthHost(Protocol):
    """Runtime facts consumed by passive status-health collection."""

    db: aiosqlite.Connection | None
    reconnecting: bool
    _startup_completed_once: bool
    _shutdown_in_progress: bool
    protected_rooms: set[str]
    bot_admin_state: dict[str, bool]
    occupants: dict[str, dict[str, dict[str, Any]]]
    admin_affiliation_query_forbidden_rooms: set[str]
    rtbl_enabled: bool
    rtbl_subscriptions: list[tuple[str, str]]
    rtbl_refresh_interval: int
    unban_task: asyncio.Task[Any] | None
    health_check_task: asyncio.Task[Any] | None
    version_check_task: asyncio.Task[Any] | None
    _rtbl_refresh_task: asyncio.Task[Any] | None
    version_check_enabled: bool
    version_check_url: str | None
    tasks: Any
    runtime_watchdog: Any
    bare_jid: Callable[[object | None], str | None]
    safe_jid: Callable[[object], str]

    async def get_db_stats(self) -> dict[str, object]: ...


class StatusMixinHost(StatusHealthHost, Protocol):
    """Cross-mixin/runtime state required by ``!status`` rendering."""

    command_prefix: str
    boundjid: Any
    bot_start_time: float
    server_connect_time: float | None
    last_reconnect_time: float | None
    last_version_check_result: str | None
    last_admin_sync_at: float | None
    last_admin_sync_ok: bool | None
    last_admin_sync_error: str | None
    pending_room_invites: dict[int, Any]
    session_lifecycle: Any
    protections: dict[str, dict[str, Any]]
    rtbl_hash_cache: dict[str, str | None]
    rtbl_domain_cache: dict[str, str | None]
    rtbl_publish_config_enabled: bool
    rtbl_publish_enabled: bool
    rtbl_publish_sanity_check_ok: bool | None
    rtbl_publish_disabled_reason: str | None
    rtbl_publish_service: str
    rtbl_publish_jid_node: str
    rtbl_publish_domain_node: str
    audit_log_retention_days: int
    redaction_enabled: bool
    last_database_backup_file: str | None
    last_database_restore_file: str | None

    async def bot_send_message(
        self,
        *,
        mto: str,
        mbody: str,
        mtype: str = "groupchat",
        **kwargs: Any,
    ) -> Any: ...

    async def outbox_runtime_state(self) -> dict[str, Any]: ...

    async def flush_redaction_index(self) -> None: ...


class AuditMixinHost(Protocol):
    """Database/messaging state required by audit persistence and commands."""

    db: aiosqlite.Connection | None
    structured_event_logs: bool
    audit_log_enabled: bool
    audit_log_retention_days: int
    last_audit_cleanup_count: int
    last_audit_cleanup_run: float
    command_prefix: str

    def _require_db(self) -> aiosqlite.Connection: ...

    async def bot_send_message(
        self,
        *,
        mto: str,
        mbody: str,
        mtype: str = "groupchat",
        **kwargs: Any,
    ) -> Any: ...


class RedactionMixinHost(Protocol):
    """XMPP, persistence and alert hooks required by message redaction."""

    db: aiosqlite.Connection | None
    plugin: Any
    protected_rooms: set[str]
    occupants: dict[str, dict[str, dict[str, Any]]]
    command_prefix: str
    redaction_enabled: bool
    redaction_index_retention_days: int
    redaction_auto_reasons: list[str]
    redaction_retract_concurrency: int
    redaction_iq_timeout_seconds: float
    auto_redact_on_imported_ban_reason: bool
    auto_redact_on_manual_muc_ban: bool
    alert_on_redaction_failure: bool
    _shutdown_in_progress: bool
    _redaction_index_pending_writes: int
    _redaction_index_last_commit: float
    _redaction_index_flush_task: asyncio.Task[None] | None
    _redaction_index_lock: asyncio.Lock
    _redaction_confirmation_waiters: dict[tuple[str, str], set[asyncio.Event]]

    def _require_db(self) -> aiosqlite.Connection: ...

    def register_handler(self, handler: Any, *args: Any, **kwargs: Any) -> Any: ...

    def make_iq_set(self, *args: Any, **kwargs: Any) -> Any: ...

    async def bot_send_message(
        self,
        *,
        mto: str,
        mbody: str,
        mtype: str = "groupchat",
        **kwargs: Any,
    ) -> Any: ...

    async def send_operational_alert(
        self,
        key: str,
        title: str,
        message: str,
        *,
        enabled: bool = True,
        details: dict[str, Any] | None = None,
    ) -> bool: ...

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


class BackupArchiveMixinHost(Protocol):
    """Path helpers required by backup archive staging."""

    def _is_backup_archive(self, backup_path: Any) -> bool: ...

    def _config_backup_path_for(self, backup_path: Any) -> Any: ...

    def _omemo_backup_path_for(self, backup_path: Any) -> Any: ...


class BackupCreateMixinHost(Protocol):
    """Persistence, archive and audit hooks required by backup creation."""

    db: aiosqlite.Connection | None
    last_database_backup_file: str | None
    _pending_database_backup_audit_events: list[
        tuple[str, str | None, str | None, str | None, dict[str, Any]]
    ]

    def _database_backup_keep(self) -> int: ...

    def _config_backup_path_for(self, backup_path: Any) -> Any: ...

    def _omemo_backup_path_for(self, backup_path: Any) -> Any: ...

    def list_database_backups(self) -> list[Any]: ...

    def _database_path(self) -> Any: ...

    def _is_backup_supported_database(self, db_path: Any | None = None) -> bool: ...

    def _database_backup_dir(self) -> Any: ...

    def _safe_backup_reason(self, reason: str) -> str: ...

    def _config_path(self) -> Any | None: ...

    def _omemo_storage_path(self) -> Any | None: ...

    def _database_backup_include_omemo(self) -> bool: ...

    def _write_backup_archive_sync(
        self,
        archive_path: Any,
        *,
        database_path: Any,
        config_path: Any | None,
        omemo_path: Any | None,
        manifest: dict[str, Any],
    ) -> None: ...

    def _has_config_backup(self, backup_path: Any) -> bool: ...

    def _has_omemo_backup(self, backup_path: Any) -> bool: ...

    def _db_backup_config_value(self, name: str, default: Any) -> Any: ...

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


class BackupRestoreMixinHost(Protocol):
    """Runtime reload and persistence hooks required by backup restore."""

    db: aiosqlite.Connection | None
    protected_rooms: set[str]
    rtbl_enabled: bool
    command_prefix: str
    last_database_restore_file: str | None

    async def close_outbox_storage(self) -> None: ...

    async def setup_db(self, *, create_startup_backup: bool = True) -> None: ...

    async def load_bans_from_db(self) -> None: ...

    async def _load_ignorelist_from_db(self) -> None: ...

    async def setup_ignorelist(self) -> None: ...

    async def _load_rtbl_subscriptions_from_db(self) -> None: ...

    async def load_pending_room_invites(self) -> None: ...

    async def _check_sqlite_integrity(self, path: Any) -> tuple[bool, str]: ...

    def resolve_database_backup(self, name: str) -> Any | None: ...

    def _database_path(self) -> Any: ...

    def _is_backup_supported_database(self, db_path: Any | None = None) -> bool: ...

    async def _backup_restore_sources(
        self,
        backup_path: Any,
        target_dir: Any,
    ) -> dict[str, Any | None]: ...

    async def create_database_backup(
        self,
        reason: str = "manual",
        *,
        prune: bool = True,
        actor: str | None = None,
        lock: bool = True,
    ) -> tuple[bool, str]: ...

    def _config_path(self) -> Any | None: ...

    def _omemo_storage_path(self) -> Any | None: ...

    def _config_backup_path_for(self, backup_path: Any) -> Any: ...

    def _omemo_backup_path_for(self, backup_path: Any) -> Any: ...

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


class BackupVerifyMixinHost(Protocol):
    """Archive/base helpers required by backup verification."""

    def resolve_database_backup(self, name: str) -> Any | None: ...

    def _is_backup_archive(self, backup_path: Any) -> bool: ...

    async def _extract_backup_archive(
        self,
        archive_path: Any,
        target_dir: Any,
        *,
        verify_checksums: bool = True,
    ) -> dict[str, Any | None]: ...

    def _config_backup_path_for(self, backup_path: Any) -> Any: ...

    def _omemo_backup_path_for(self, backup_path: Any) -> Any: ...


class BackupCommandMixinHost(Protocol):
    """Managed-backup operations exposed through admin commands."""

    command_prefix: str

    async def create_database_backup(
        self,
        reason: str = "manual",
        *,
        prune: bool = True,
        actor: str | None = None,
        lock: bool = True,
    ) -> tuple[bool, str]: ...

    def _backup_companion_names(self, backup_path: Any) -> list[str]: ...

    def list_database_backups(self) -> list[Any]: ...

    def _database_backup_dir(self) -> Any: ...

    def _database_backup_keep(self) -> int: ...

    def _format_backup_entry(self, backup: Any, index: int | None = None) -> str: ...

    def resolve_database_backup(self, name: str) -> Any | None: ...

    def _format_backup_details(self, backup: Any) -> str: ...

    async def verify_database_backup(self, name: str, *, lock: bool = True) -> tuple[bool, str]: ...

    async def delete_database_backup(
        self,
        name: str,
        *,
        actor: str | None = None,
        lock: bool = True,
    ) -> tuple[bool, str]: ...

    async def restore_database_backup(
        self,
        name: str,
        *,
        actor: str | None = None,
    ) -> tuple[bool, str]: ...

    async def bot_send_message(
        self,
        *,
        mto: str,
        mbody: str,
        mtype: str = "groupchat",
        **kwargs: Any,
    ) -> Any: ...


class CommandBackupMixinHost(Protocol):
    """Command-router hooks for backup and restore commands."""

    def _actor_jid_from_room_nick(self, room: str, nick: str) -> str: ...

    async def cmd_backup(
        self,
        args: list[str],
        room: str,
        actor: str | None = None,
    ) -> None: ...

    async def cmd_restore(
        self,
        args: list[str],
        room: str,
        actor: str | None = None,
    ) -> None: ...


class ImportExportMixinHost(Protocol):
    """Ban/cache/database capabilities required by CSV import and export."""

    db: aiosqlite.Connection | None
    ban_cache: dict[str, BanTuple]
    command_prefix: str

    def _require_db(self) -> aiosqlite.Connection: ...

    async def find_active_jid_ban_by_nick(
        self,
        nick: str | None,
    ) -> tuple[str, int, str | None, str | None] | None: ...

    def _cache_ban(
        self,
        jid: str | None,
        nick: str | None,
        until: int,
        issuer: str | None,
        comment: str | None,
    ) -> None: ...

    async def load_bans_from_db(self) -> None: ...

    async def create_database_backup(
        self,
        reason: str = "manual",
        *,
        prune: bool = True,
        actor: str | None = None,
        lock: bool = True,
    ) -> tuple[bool, str]: ...

    async def maybe_auto_redact_after_imported_ban(
        self,
        jid: str | None,
        comment: str | None,
        actor: str | None = None,
    ) -> None: ...

    async def bot_send_message(
        self,
        *,
        mto: str,
        mbody: str,
        mtype: str = "groupchat",
        **kwargs: Any,
    ) -> Any: ...


class CommandImportExportMixinHost(Protocol):
    """CSV import/export operations required by command routing."""

    command_prefix: str
    last_database_backup_file: str | None

    def _actor_jid_from_room_nick(self, room: str, nick: str) -> str: ...

    async def import_bans_from_csv(
        self,
        filename: str,
        *,
        actor: str | None = None,
        dry_run: bool = False,
    ) -> tuple[int, int, list[str]]: ...

    async def export_bans_to_csv(self) -> tuple[bool, str]: ...

    async def cmd_export(self, args: list[str], room: str) -> None: ...

    async def bot_send_message(
        self,
        *,
        mto: str,
        mbody: str,
        mtype: str = "groupchat",
        **kwargs: Any,
    ) -> Any: ...

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


class ConfigSnapshotMixinHost(Protocol):
    """Live attributes required to build runtime/startup config snapshots."""

    CONFIG_KEYS: tuple[str, ...]
    STARTUP_ONLY_CONFIG_KEYS: tuple[str, ...]
    command_prefix: str
    announce_startup: bool
    announce_sync_details: bool
    show_ban_in_muc: bool
    allow_user_cmds: bool
    allow_admin_commands_in_dms: bool
    room_invites_enabled: bool
    room_invite_max_age_days: int
    health_check_interval: float
    unban_check_interval: float
    max_tempban_days: int
    public_command_rate_limit_window: int
    public_command_rate_limit_max: int
    muc_write_limit: int
    sync_batch_size: int
    structured_event_logs: bool
    audit_log_enabled: bool
    audit_log_retention_days: int
    rtbl_announce: bool
    rtbl_refresh_interval: int
    redaction_enabled: bool
    redaction_index_retention_days: int
    redaction_auto_reasons: list[str]
    version_check_enabled: bool
    version_check_interval: int
    version_check_url: str | None


class VCardMixinHost(Protocol):
    """Slixmpp/profile state required by vCard and avatar publication."""

    room_bot_nicks: dict[str, str]
    avatar_hash: str | None
    boundjid: Any

    def __getitem__(self, key: str) -> Any: ...

    def make_presence(self, **kwargs: Any) -> Any: ...

    def is_connected(self) -> bool: ...


class ConfigDisplayMixinHost(Protocol):
    """Schema key sets required by configuration display helpers."""

    CONFIG_KEYS: tuple[str, ...]
    CONFIG_SECRET_KEYS: set[str]
    STARTUP_ONLY_CONFIG_KEYS: tuple[str, ...]
    CONFIG_NEVER_WRITABLE_KEYS: set[str]


class ConfigRuntimeMixinHost(Protocol):
    """Cross-mixin hooks required by runtime configuration mutation/reload."""

    CONFIG_KEYS: tuple[str, ...]
    STARTUP_ONLY_CONFIG_KEYS: tuple[str, ...]
    CONFIG_NEVER_WRITABLE_KEYS: set[str]
    muc_write_limit: int
    muc_write_semaphore: asyncio.Semaphore

    def _validate_config(self) -> tuple[list[str], list[str]]: ...

    def _format_config_validation(
        self,
        errors: list[str],
        warnings: list[str],
    ) -> str: ...

    def _config_file_path(self) -> Path: ...

    def _config_default_values_from_sample(self) -> dict[str, Any]: ...

    def _runtime_config_snapshot(self) -> dict[str, object]: ...

    def _startup_config_snapshot(self) -> dict[str, object]: ...

    def _format_startup_only_changes(
        self,
        before: dict[str, object],
        after: dict[str, object],
    ) -> list[str]: ...

    def _snapshot_config_values(self, keys: Iterable[str]) -> dict[str, object]: ...

    def _restore_config_values(self, values: dict[str, object]) -> None: ...

    async def update_vcard(self) -> bool: ...

    async def create_database_backup(
        self,
        reason: str = "manual",
        *,
        prune: bool = True,
        actor: str | None = None,
        lock: bool = True,
    ) -> tuple[bool, str]: ...


class ConfigCommandMixinHost(Protocol):
    """Messaging/audit state required by the admin config command surface."""

    command_prefix: str
    config_output_mode: str
    omemo_auto_encrypt_admin_room: bool
    omemo_plaintext_fallback: bool
    omemo_reset_on_identity_change: bool
    rtbl_publish_enabled: bool
    rtbl_publish_service: str
    rtbl_publish_jid_node: str
    rtbl_publish_domain_node: str

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


class IgnorelistMixinHost(Protocol):
    """Database, moderation and messaging hooks required by IgnorelistMixin."""

    db: aiosqlite.Connection | None
    command_prefix: str
    ignore_jids: set[str]
    ignore_domains: set[str]
    bare_jid: Callable[[object | None], str | None]

    def _require_db(self) -> aiosqlite.Connection: ...

    async def unban_all(
        self,
        identifier: str,
        issuer: str | None = None,
        *,
        notify_policy: bool = True,
    ) -> bool: ...

    async def bot_send_message(
        self,
        *,
        mto: str,
        mbody: str,
        mtype: str = "groupchat",
        **kwargs: Any,
    ) -> Any: ...

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


class CommandIgnoreMixinHost(Protocol):
    """Actor resolution and ignorelist command surface required by routing."""

    def _actor_jid_from_room_nick(self, room: str, nick: str) -> str: ...

    async def cmd_ignore(
        self,
        args: list[str],
        room: str,
        actor: str = "unknown",
        command_name: str = "ignore",
    ) -> None: ...


class CommandPolicyMixinHost(Protocol):
    """Persistence and messaging hooks required by policy/rules commands."""

    command_prefix: str
    protected_rooms: set[str]

    async def get_public_policy(self) -> tuple[bool, str]: ...

    async def set_public_policy_text(self, text: str, enabled: bool = True) -> None: ...

    async def set_public_policy_enabled(self, enabled: bool) -> None: ...

    async def clear_public_policy(self) -> None: ...

    def _policy_usage_text(self) -> str: ...

    async def bot_send_message(
        self,
        *,
        mto: str,
        mbody: str,
        mtype: str = "groupchat",
        **kwargs: Any,
    ) -> Any: ...


class CommandUsageMixinHost(Protocol):
    """Command-prefix state required by focused usage renderers."""

    command_prefix: str


class CommandHelpMixinHost(Protocol):
    """Policy state and focused usage helpers required by help rendering."""

    command_prefix: str
    help_output_mode: str

    async def get_public_policy(self) -> tuple[bool, str]: ...

    def _help_usage_text(self) -> str: ...
    def _room_usage_text(self) -> str: ...
    def _room_invite_usage_text(self) -> str: ...
    def _redact_usage_text(self) -> str: ...
    def _policy_usage_text(self) -> str: ...
    def _backup_usage_text(self) -> str: ...
    def _restore_usage_text(self) -> str: ...
    def _export_usage_text(self) -> str: ...
    def _import_usage_text(self) -> str: ...
    def _rtbl_usage_text(self) -> str: ...
    def _rtbl_publish_usage_text(self) -> str: ...
    def _ignore_usage_text(self) -> str: ...
    def _config_usage_text(self) -> str: ...
    def _audit_usage_text(self) -> str: ...
    def _ban_usage_text(self) -> str: ...
    def _tempban_usage_text(self) -> str: ...
    def _unban_usage_text(self) -> str: ...
    def _banlist_usage_text(self) -> str: ...
    def _bansearch_usage_text(self) -> str: ...
    def _baninfo_usage_text(self) -> str: ...
    def _history_usage_text(self) -> str: ...
    def _banedit_usage_text(self) -> str: ...
    def _why_usage_text(self) -> str: ...
    def _restart_usage_text(self) -> str: ...
    def _reload_usage_text(self) -> str: ...
    def _checkupdate_usage_text(self) -> str: ...
    def _status_usage_text(self) -> str: ...
    def _tasks_usage_text(self) -> str: ...
    def _whoami_usage_text(self) -> str: ...
    def _sync_usage_text(self) -> str: ...
    def _syncadmins_usage_text(self) -> str: ...
    def _syncbans_usage_text(self) -> str: ...
    def _omemo_usage_text(self) -> str: ...
    def _protection_usage_text(self) -> str: ...
    def _report_usage_text(self) -> str: ...
