"""Static host contracts for configuration snapshots, display, runtime, and commands."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from pathlib import Path
from typing import Any, Protocol


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
