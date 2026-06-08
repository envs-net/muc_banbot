"""Managed backup base helpers."""

from __future__ import annotations

import logging
import pathlib
import zipfile
from typing import Any

import config

from ..managed_files import format_file_size, list_managed_files, resolve_managed_file
from .common import DatabaseBackup, _BACKUP_CONFIG_ENTRY, _BACKUP_OMEMO_ENTRY, _BACKUP_SAFE_RE

log = logging.getLogger(__name__)

class BackupBaseMixin:

    def _db_backup_config_value(self, name: str, default: Any) -> Any:
        if config is None:
            return default
        return getattr(config, name, default)

    def _database_path(self) -> pathlib.Path:
        db_file = str(self._db_backup_config_value("DB_FILE", "banbot.db")).strip()
        return pathlib.Path(db_file).expanduser()

    def _database_backup_dir(self) -> pathlib.Path:
        raw_dir = str(self._db_backup_config_value("DB_BACKUP_DIR", "data/backups")).strip()
        return pathlib.Path(raw_dir or "data/backups").expanduser()

    def _database_backup_keep(self) -> int:
        try:
            return max(1, int(self._db_backup_config_value("DB_BACKUP_KEEP", 15)))
        except Exception:
            return 15

    def _database_backup_include_omemo(self) -> bool:
        return bool(self._db_backup_config_value("DB_BACKUP_INCLUDE_OMEMO", True))

    def _database_backup_pattern(self) -> str:
        return f"{self._database_path().name}.snapshot-*"

    def _config_path(self) -> pathlib.Path | None:
        """Return the active config.py path when it can be resolved safely."""
        raw_path = getattr(config, "__file__", None) if config is not None else None
        if raw_path:
            path = pathlib.Path(str(raw_path)).expanduser()
            if path.name == "config.py":
                return path

        fallback = pathlib.Path("config.py").expanduser()
        if fallback.exists() and fallback.is_file():
            return fallback
        return None

    def _omemo_storage_path(self) -> pathlib.Path | None:
        """Return OMEMO storage path if configured."""
        raw_path = self._db_backup_config_value("OMEMO_STORAGE_FILE", None)
        if raw_path in (None, ""):
            return None
        return pathlib.Path(str(raw_path)).expanduser()

    def _config_backup_path_for(self, backup_path: pathlib.Path) -> pathlib.Path:
        """Return the legacy companion config.py backup path for a database snapshot."""
        return backup_path.with_name(f"{backup_path.name}.config.py")

    def _omemo_backup_path_for(self, backup_path: pathlib.Path) -> pathlib.Path:
        """Return the legacy companion OMEMO storage backup path for a database snapshot."""
        return backup_path.with_name(f"{backup_path.name}.omemo.json")

    def _is_backup_archive(self, backup_path: pathlib.Path) -> bool:
        """Return True when the managed backup is a ZIP archive."""
        return backup_path.suffix.lower() == ".zip"

    def _backup_archive_names(self, backup_path: pathlib.Path) -> set[str]:
        """Return archive member names for a ZIP backup, or an empty set."""
        if not self._is_backup_archive(backup_path) or not backup_path.is_file():
            return set()
        try:
            with zipfile.ZipFile(backup_path, "r") as archive:
                return set(archive.namelist())
        except (OSError, zipfile.BadZipFile):
            return set()

    def _has_config_backup(self, backup_path: pathlib.Path) -> bool:
        if self._is_backup_archive(backup_path):
            return _BACKUP_CONFIG_ENTRY in self._backup_archive_names(backup_path)
        return self._config_backup_path_for(backup_path).is_file()

    def _has_omemo_backup(self, backup_path: pathlib.Path) -> bool:
        if self._is_backup_archive(backup_path):
            return _BACKUP_OMEMO_ENTRY in self._backup_archive_names(backup_path)
        return self._omemo_backup_path_for(backup_path).is_file()

    def _is_backup_supported_database(self, db_path: pathlib.Path | None = None) -> bool:
        path = db_path or self._database_path()
        return str(path) not in ("", ":memory:")

    def _safe_backup_reason(self, reason: str) -> str:
        cleaned = _BACKUP_SAFE_RE.sub("-", str(reason).strip().lower()).strip("-._")
        return cleaned or "manual"

    def _format_backup_size(self, size: int) -> str:
        return format_file_size(size)

    def _backup_companion_names(self, backup_path: pathlib.Path) -> list[str]:
        companions: list[str] = []
        if self._has_config_backup(backup_path):
            companions.append("config.py")
        if self._has_omemo_backup(backup_path):
            companions.append("omemo.json")
        return companions

    def _format_backup_entry(self, backup: DatabaseBackup, index: int | None = None) -> str:
        prefix = f"{index}. " if index is not None else ""
        companions = self._backup_companion_names(backup.path)
        companion_suffix = f", {', '.join(companions)}" if companions else ""
        return f"{prefix}{backup.name} ({self._format_backup_size(backup.size)}, {backup.mtime_text}{companion_suffix})"

    def _is_database_backup_file(self, path: pathlib.Path) -> bool:
        return path.is_file() and not path.name.endswith((".config.py", ".omemo.json"))

    def list_database_backups(self) -> list[DatabaseBackup]:
        """Return managed backup files sorted newest first."""
        return list_managed_files(
            self._database_backup_dir(),
            self._database_backup_pattern(),
            exclude_suffixes=(".config.py", ".omemo.json"),
            predicate=self._is_database_backup_file,
        )

    def resolve_database_backup(self, name: str) -> DatabaseBackup | None:
        """Resolve a backup by basename, path inside backup dir, or 'latest'."""
        backups = self.list_database_backups()
        path = resolve_managed_file(
            self._database_backup_dir(),
            name,
            backups,
            predicate=self._is_database_backup_file,
        )
        if path is None:
            return None
        try:
            stat = path.stat()
        except OSError:
            return None
        return DatabaseBackup(path=path, size=stat.st_size, mtime=stat.st_mtime)

    def _format_backup_details(self, backup: DatabaseBackup) -> str:
        companions = self._backup_companion_names(backup.path)
        content_label = "Archive entries" if self._is_backup_archive(backup.path) else "Companions"
        lines = [
            f"💾 Backup: {backup.name}",
            f"Path: {backup.path}",
            f"Format: {'ZIP archive' if self._is_backup_archive(backup.path) else 'legacy snapshot'}",
            f"Size: {self._format_backup_size(backup.size)}",
            f"Modified: {backup.mtime_text}",
            f"{content_label}: " + (", ".join(companions) if companions else "none"),
        ]
        return "\n".join(lines)
