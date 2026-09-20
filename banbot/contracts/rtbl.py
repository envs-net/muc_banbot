"""Static host contracts for RTBL commands, storage, PubSub, and application."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol

import aiosqlite

from .common import ActorJidResolverHost


class CommandRtblMixinHost(ActorJidResolverHost, Protocol):
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
