"""Managed backup creation helpers."""

from __future__ import annotations

import asyncio
import logging
import os
import pathlib
import shutil
import sqlite3
import tempfile
from datetime import datetime
from typing import Any

from ..locks import database_file_lock
from ..managed_files import prune_managed_files
from .common import _BACKUP_CONFIG_ENTRY, _BACKUP_DATABASE_ENTRY, _BACKUP_FORMAT, _BACKUP_OMEMO_ENTRY

log = logging.getLogger(__name__)

class BackupCreateMixin:

    async def prune_database_backups(self, *, preserve: pathlib.Path | None = None) -> list[pathlib.Path]:
        """Delete old managed backups beyond DB_BACKUP_KEEP."""
        keep = self._database_backup_keep()

        def companions(path: pathlib.Path) -> list[pathlib.Path]:
            return [
                self._config_backup_path_for(path),
                self._omemo_backup_path_for(path),
            ]

        try:
            removed = await prune_managed_files(
                self.list_database_backups(),
                keep=keep,
                preserve=preserve,
                delete_companions=companions,
            )
        except OSError as exc:
            log.warning("Failed to prune database backups: %s", exc)
            return []

        for path in removed:
            log.info("Deleted old database backup: %s", path)
        return removed

    def _queue_database_backup_audit_event(
        self,
        event_type: str,
        *,
        actor: str | None,
        target_type: str | None = None,
        target: str | None = None,
        details: dict[str, Any],
    ) -> None:
        """Remember backup audit events until the SQLite audit table is ready."""
        pending = getattr(self, "_pending_database_backup_audit_events", None)
        if pending is None:
            pending = []
            self._pending_database_backup_audit_events = pending
        pending.append((event_type, actor, target_type, target, details))

    async def flush_pending_database_backup_audit_events(self) -> None:
        """Persist backup audit events that were created before setup_db opened SQLite."""
        pending = list(getattr(self, "_pending_database_backup_audit_events", []))
        if not pending:
            return
        if not getattr(self, "db", None) or not hasattr(self, "audit_event"):
            return

        remaining: list[tuple[str, str | None, str | None, str | None, dict[str, Any]]] = []
        for event_type, actor, target_type, target, details in pending:
            try:
                await self.audit_event(
                    event_type,
                    actor=actor,
                    target_type=target_type,
                    target=target,
                    details=details,
                )
            except Exception as exc:
                log.debug("Failed to flush pending backup audit event %s: %s", event_type, exc)
                remaining.append((event_type, actor, target_type, target, details))
        self._pending_database_backup_audit_events = remaining

    async def _copy_database_to_backup(self, db_path: pathlib.Path, backup_path: pathlib.Path) -> None:
        """Create a consistent database snapshot using SQLite's online backup API when possible."""
        if getattr(self, "db", None):
            await self.db.commit()
            destination = sqlite3.connect(str(backup_path))
            try:
                await self.db.backup(destination)
            finally:
                destination.close()
        else:
            await asyncio.to_thread(shutil.copy2, db_path, backup_path)

    async def create_database_backup(
        self,
        reason: str = "manual",
        *,
        prune: bool = True,
        actor: str | None = None,
        lock: bool = True,
    ) -> tuple[bool, str]:
        """Create a timestamped self-contained ZIP backup and optionally prune old backups."""
        if lock:
            async with database_file_lock(self):
                return await self.create_database_backup(reason, prune=prune, actor=actor, lock=False)

        db_path = self._database_path()
        self.last_database_backup_file = None

        if not self._is_backup_supported_database(db_path):
            return False, "Database backups are not available for in-memory DB_FILE."
        if not db_path.exists():
            return False, f"Database file does not exist: {db_path}"
        if not db_path.is_file():
            return False, f"Database path is not a regular file: {db_path}"

        backup_dir = self._database_backup_dir()
        backup_dir.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(backup_dir, 0o700)
        except OSError as exc:
            log.debug("Failed to restrict backup directory permissions for %s: %s", backup_dir, exc)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        reason_slug = self._safe_backup_reason(reason)
        base_name = f"{db_path.name}.snapshot-{reason_slug}-{timestamp}"
        backup_path = backup_dir / f"{base_name}.zip"
        counter = 1
        while backup_path.exists():
            backup_path = backup_dir / f"{base_name}-{counter}.zip"
            counter += 1

        try:
            with tempfile.TemporaryDirectory(prefix="banbot-backup-create-") as tmp_name:
                tmp_dir = pathlib.Path(tmp_name)
                database_copy = tmp_dir / _BACKUP_DATABASE_ENTRY
                await self._copy_database_to_backup(db_path, database_copy)

                config_path = self._config_path()
                config_source: pathlib.Path | None = None
                if config_path is not None and config_path.exists() and config_path.is_file():
                    config_source = config_path

                omemo_path = self._omemo_storage_path()
                omemo_source: pathlib.Path | None = None
                if self._database_backup_include_omemo():
                    if omemo_path is not None and omemo_path.exists() and omemo_path.is_file():
                        omemo_source = omemo_path
                    else:
                        log.debug("OMEMO backup skipped: storage file does not exist or is not configured")

                manifest = {
                    "format": _BACKUP_FORMAT,
                    "created_at": int(datetime.now().timestamp()),
                    "created_at_text": datetime.now().isoformat(timespec="seconds"),
                    "reason": reason_slug,
                    "database": {
                        "source": str(db_path),
                        "entry": _BACKUP_DATABASE_ENTRY,
                    },
                    "contains": {
                        "database": True,
                        "config": config_source is not None,
                        "omemo": omemo_source is not None,
                    },
                    "entries": {
                        "database": _BACKUP_DATABASE_ENTRY,
                        "config": _BACKUP_CONFIG_ENTRY if config_source is not None else None,
                        "omemo": _BACKUP_OMEMO_ENTRY if omemo_source is not None else None,
                    },
                }

                await asyncio.to_thread(
                    self._write_backup_archive_sync,
                    backup_path,
                    database_path=database_copy,
                    config_path=config_source,
                    omemo_path=omemo_source,
                    manifest=manifest,
                )

            try:
                os.chmod(backup_path, 0o600)
            except OSError as exc:
                log.debug("Failed to restrict backup archive permissions for %s: %s", backup_path, exc)

            self.last_database_backup_file = str(backup_path)
            if prune:
                await self.prune_database_backups(preserve=backup_path)

            config_backup = _BACKUP_CONFIG_ENTRY if self._has_config_backup(backup_path) else None
            omemo_backup = _BACKUP_OMEMO_ENTRY if self._has_omemo_backup(backup_path) else None

            log.info("Created database backup archive: %s", backup_path)
            if hasattr(self, "log_event"):
                try:
                    self.log_event(
                        logging.INFO,
                        "db_backup_created",
                        actor=actor or "system",
                        backup=str(backup_path),
                        config_backup=config_backup,
                        omemo_backup=omemo_backup,
                        reason=reason_slug,
                    )
                except Exception as exc:
                    log.debug("Failed to write backup structured event: %s", exc)
            audit_actor = actor or "system"
            audit_details = {
                "backup": str(backup_path),
                "backup_format": _BACKUP_FORMAT,
                "config_backup": config_backup,
                "omemo_backup": omemo_backup,
                "reason": reason_slug,
            }
            if hasattr(self, "audit_event"):
                if getattr(self, "db", None):
                    try:
                        await self.audit_event(
                            "db_backup_created",
                            actor=audit_actor,
                            target_type="backup",
                            target=backup_path.name,
                            details=audit_details,
                        )
                    except Exception as exc:
                        log.debug("Failed to audit database backup: %s", exc)
                else:
                    self._queue_database_backup_audit_event(
                        "db_backup_created",
                        actor=audit_actor,
                        target_type="backup",
                        target=backup_path.name,
                        details=audit_details,
                    )
            return True, str(backup_path)
        except Exception as exc:
            log.error("Failed to create database backup: %s", exc)
            return False, str(exc)

    async def create_startup_database_snapshot(self) -> tuple[bool, str]:
        """Create an automatic startup snapshot when enabled and possible."""
        if not bool(self._db_backup_config_value("DB_BACKUP_ON_START", True)):
            return False, "Startup database backups are disabled."
        return await self.create_database_backup("startup", actor="system")
