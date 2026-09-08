"""Managed backup archive helpers."""

from __future__ import annotations

import asyncio
import logging
import pathlib
import zipfile
from typing import Any

from envs_xmpp_core.storage.archive import extract_zip_member
from envs_xmpp_core.storage.backup import (
    BackupArchiveSource,
    build_backup_archive,
    verify_backup_archive,
)

from .common import (
    _BACKUP_CONFIG_ENTRY,
    _BACKUP_DATABASE_ENTRY,
    _BACKUP_FORMAT,
    _BACKUP_MANIFEST_ENTRY,
    _BACKUP_OMEMO_ENTRY,
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
        """Write one self-contained ZIP backup archive atomically."""
        sources = [
            BackupArchiveSource(
                _BACKUP_DATABASE_ENTRY,
                database_path,
                source=manifest.get("database", {}).get("source", database_path),
                required=True,
            )
        ]
        if config_path is not None:
            sources.append(
                BackupArchiveSource(_BACKUP_CONFIG_ENTRY, config_path, source=config_path)
            )
        if omemo_path is not None:
            sources.append(
                BackupArchiveSource(_BACKUP_OMEMO_ENTRY, omemo_path, source=omemo_path)
            )
        build_backup_archive(
            archive_path,
            sources=sources,
            manifest=manifest,
            manifest_name=_BACKUP_MANIFEST_ENTRY,
            json_newline=True,
        )

    @staticmethod
    def _extract_backup_archive_sync(
        archive_path: pathlib.Path,
        target_dir: pathlib.Path,
        *,
        verify_checksums: bool = True,
    ) -> dict[str, pathlib.Path | None]:
        """Verify and extract known backup archive entries into target_dir."""
        verification = verify_backup_archive(
            archive_path,
            manifest_name=_BACKUP_MANIFEST_ENTRY,
            expected_fields={"format": _BACKUP_FORMAT},
            required_members=[_BACKUP_DATABASE_ENTRY],
            allow_legacy_without_files=True,
            verify_checksums=verify_checksums,
        )
        if not verification.ok:
            raise ValueError("Invalid backup archive: " + "; ".join(verification.errors))

        with zipfile.ZipFile(archive_path, "r") as archive:
            names = set(verification.members)
            database_path = extract_zip_member(
                archive,
                _BACKUP_DATABASE_ENTRY,
                target_dir / _BACKUP_DATABASE_ENTRY,
            )

            config_path: pathlib.Path | None = None
            if _BACKUP_CONFIG_ENTRY in names:
                config_path = extract_zip_member(
                    archive,
                    _BACKUP_CONFIG_ENTRY,
                    target_dir / _BACKUP_CONFIG_ENTRY,
                )

            omemo_path: pathlib.Path | None = None
            if _BACKUP_OMEMO_ENTRY in names:
                omemo_path = extract_zip_member(
                    archive,
                    _BACKUP_OMEMO_ENTRY,
                    target_dir / _BACKUP_OMEMO_ENTRY,
                )

        return {
            "database": database_path,
            "config": config_path,
            "omemo": omemo_path,
        }

    async def _extract_backup_archive(
        self,
        archive_path: pathlib.Path,
        target_dir: pathlib.Path,
        *,
        verify_checksums: bool = True,
    ) -> dict[str, pathlib.Path | None]:
        """Extract a ZIP backup archive without blocking the event loop."""
        return await asyncio.to_thread(
            self._extract_backup_archive_sync,
            archive_path,
            target_dir,
            verify_checksums=verify_checksums,
        )

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
