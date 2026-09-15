"""Managed backup archive helpers."""

from __future__ import annotations

import logging
import pathlib
from typing import TYPE_CHECKING, Any

from envs_xmpp_core.storage.backup import (
    BackupArchiveEntrySpec,
    BackupArchiveError,
    BackupArchiveSource,
    build_backup_archive,
    stage_backup_archive,
)

from ..managed_io import run_blocking_io
from .common import (
    _BACKUP_CONFIG_ENTRY,
    _BACKUP_DATABASE_ENTRY,
    _BACKUP_FORMAT,
    _BACKUP_MANIFEST_ENTRY,
    _BACKUP_OMEMO_ENTRY,
)

log = logging.getLogger(__name__)

if TYPE_CHECKING:
    from ..contracts import BackupArchiveMixinHost

    class _BackupArchiveMixinContract(BackupArchiveMixinHost):
        pass
else:
    class _BackupArchiveMixinContract:
        pass


class BackupArchiveMixin(_BackupArchiveMixinContract):

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
        """Verify and stage known backup members through the shared archive core."""
        try:
            staged = stage_backup_archive(
                archive_path,
                target_dir,
                entries=[
                    BackupArchiveEntrySpec(
                        "database",
                        _BACKUP_DATABASE_ENTRY,
                        required=True,
                    ),
                    BackupArchiveEntrySpec("config", _BACKUP_CONFIG_ENTRY),
                    BackupArchiveEntrySpec("omemo", _BACKUP_OMEMO_ENTRY),
                ],
                manifest_name=_BACKUP_MANIFEST_ENTRY,
                expected_fields={"format": _BACKUP_FORMAT},
                allow_legacy_without_files=True,
                verify_checksums=verify_checksums,
            )
        except BackupArchiveError as exc:
            raise ValueError(f"Invalid backup archive: {exc}") from exc
        return dict(staged.entries)

    async def _extract_backup_archive(
        self,
        archive_path: pathlib.Path,
        target_dir: pathlib.Path,
        *,
        verify_checksums: bool = True,
    ) -> dict[str, pathlib.Path | None]:
        """Extract a ZIP backup archive without blocking the event loop."""
        return await run_blocking_io(
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
