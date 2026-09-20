"""Static host contracts for backup creation, verification, restore, and commands."""

from __future__ import annotations

from typing import Any, Protocol

import aiosqlite

from .common import ActorJidResolverHost


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



class CommandBackupMixinHost(ActorJidResolverHost, Protocol):
    """Command-router hooks for backup and restore commands."""

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
