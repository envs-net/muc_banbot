"""Managed backup mixin helpers."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import pathlib
import shutil
import sqlite3
import tempfile
import zipfile
from datetime import datetime
from typing import Any

import config

from ..locks import database_file_lock
from ..managed_files import list_managed_files, prune_managed_files
from ..utils import get_list_page_size, paginate_lines, resolve_page, wants_all_pages, without_all_pages_arg
from .common import (
    DatabaseBackup,
    _BACKUP_CONFIG_ENTRY,
    _BACKUP_DATABASE_ENTRY,
    _BACKUP_FORMAT,
    _BACKUP_MANIFEST_ENTRY,
    _BACKUP_OMEMO_ENTRY,
    _BACKUP_SAFE_RE,
)

log = logging.getLogger(__name__)

class BackupArchiveMixin:

    @staticmethod
    def _write_backup_archive_sync(
        archive_path: pathlib.Path,
        *,
        database_path: pathlib.Path,
        config_path: pathlib.Path | None,
        omemo_path: pathlib.Path | None,
        manifest: dict[str, Any],
    ) -> None:
        """Write one self-contained ZIP backup archive."""
        with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(
                _BACKUP_MANIFEST_ENTRY,
                json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            )
            archive.write(database_path, _BACKUP_DATABASE_ENTRY)
            if config_path is not None:
                archive.write(config_path, _BACKUP_CONFIG_ENTRY)
            if omemo_path is not None:
                archive.write(omemo_path, _BACKUP_OMEMO_ENTRY)

    @staticmethod
    def _extract_backup_archive_sync(archive_path: pathlib.Path, target_dir: pathlib.Path) -> dict[str, pathlib.Path | None]:
        """Extract known backup archive entries into target_dir."""
        try:
            archive = zipfile.ZipFile(archive_path, "r")
        except zipfile.BadZipFile as exc:
            raise ValueError(f"Invalid backup archive: {exc}") from exc

        with archive:
            names = set(archive.namelist())
            if _BACKUP_MANIFEST_ENTRY not in names:
                raise ValueError("Backup archive is missing manifest.json")
            if _BACKUP_DATABASE_ENTRY not in names:
                raise ValueError("Backup archive is missing database.sqlite3")

            manifest_text = archive.read(_BACKUP_MANIFEST_ENTRY).decode("utf-8")
            manifest = json.loads(manifest_text)
            if manifest.get("format") != _BACKUP_FORMAT:
                raise ValueError(f"Unsupported backup format: {manifest.get('format')!r}")

            database_path = target_dir / _BACKUP_DATABASE_ENTRY
            with archive.open(_BACKUP_DATABASE_ENTRY, "r") as src, database_path.open("wb") as dst:
                shutil.copyfileobj(src, dst)

            config_path: pathlib.Path | None = None
            if _BACKUP_CONFIG_ENTRY in names:
                config_path = target_dir / _BACKUP_CONFIG_ENTRY
                with archive.open(_BACKUP_CONFIG_ENTRY, "r") as src, config_path.open("wb") as dst:
                    shutil.copyfileobj(src, dst)

            omemo_path: pathlib.Path | None = None
            if _BACKUP_OMEMO_ENTRY in names:
                omemo_path = target_dir / _BACKUP_OMEMO_ENTRY
                with archive.open(_BACKUP_OMEMO_ENTRY, "r") as src, omemo_path.open("wb") as dst:
                    shutil.copyfileobj(src, dst)

        return {
            "database": database_path,
            "config": config_path,
            "omemo": omemo_path,
        }

    async def _extract_backup_archive(self, archive_path: pathlib.Path, target_dir: pathlib.Path) -> dict[str, pathlib.Path | None]:
        """Extract a ZIP backup archive without blocking the event loop."""
        return await asyncio.to_thread(self._extract_backup_archive_sync, archive_path, target_dir)

    async def _backup_restore_sources(
        self, backup_path: pathlib.Path, target_dir: pathlib.Path
    ) -> dict[str, pathlib.Path | None]:
        """Return database/config/OMEMO source paths for archive or legacy backups."""
        if self._is_backup_archive(backup_path):
            return await self._extract_backup_archive(backup_path, target_dir)
        return {
            "database": backup_path,
            "config": self._config_backup_path_for(backup_path) if self._config_backup_path_for(backup_path).is_file() else None,
            "omemo": self._omemo_backup_path_for(backup_path) if self._omemo_backup_path_for(backup_path).is_file() else None,
        }
