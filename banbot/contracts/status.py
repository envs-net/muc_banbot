"""Static host contracts for health collection and status presentation."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any, Protocol

import aiosqlite


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
