"""Static host contracts for runtime commands and supporting operational mixins."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

import aiosqlite
from envs_xmpp_core.release.state import ReleaseState

from ..cache import BanTuple
from .common import ActorJidResolverHost


class CommandEntryPointMixinHost(ActorJidResolverHost, Protocol):
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



class CommandRuntimeMixinHost(ActorJidResolverHost, Protocol):
    """Runtime command and restart lifecycle hooks required by command handlers."""

    command_prefix: str
    version_check_url: str | None
    tasks: Any
    runtime_watchdog: Any
    _restart_task: asyncio.Task[Any] | None
    _restart_schedule_lock: asyncio.Lock
    _shutdown_in_progress: bool
    _shutdown_complete: bool

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



class CommandImportExportMixinHost(ActorJidResolverHost, Protocol):
    """CSV import/export operations required by command routing."""

    command_prefix: str
    last_database_backup_file: str | None

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



class VCardMixinHost(Protocol):
    """Slixmpp/profile state required by vCard and avatar publication."""

    room_bot_nicks: dict[str, str]
    avatar_hash: str | None
    boundjid: Any

    def __getitem__(self, key: str) -> Any: ...

    def make_presence(self, **kwargs: Any) -> Any: ...

    def is_connected(self) -> bool: ...



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



class CommandIgnoreMixinHost(ActorJidResolverHost, Protocol):
    """Actor resolution and ignorelist command surface required by routing."""

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
