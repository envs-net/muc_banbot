"""Managed backup verification helpers."""

from __future__ import annotations

import json
import logging
import pathlib
import tempfile
from typing import TYPE_CHECKING

from envs_xmpp_core.storage.backup import verify_backup_archive
from envs_xmpp_core.storage.sqlite import check_sqlite_integrity

from ..locks import database_file_lock
from ..managed_io import run_blocking_io
from .common import _BACKUP_DATABASE_ENTRY, _BACKUP_FORMAT, _BACKUP_MANIFEST_ENTRY

log = logging.getLogger(__name__)

if TYPE_CHECKING:
    from ..contracts import BackupVerifyMixinHost

    class _BackupVerifyMixinContract(BackupVerifyMixinHost):
        pass
else:
    class _BackupVerifyMixinContract:
        pass


class BackupVerifyMixin(_BackupVerifyMixinContract):

    @staticmethod
    def _check_sqlite_integrity_sync(path: pathlib.Path) -> tuple[bool, str]:
        """Run SQLite PRAGMA integrity_check for a database file."""
        result = check_sqlite_integrity(path, require_nonempty_file=True)
        return result.ok, result.message

    async def _check_sqlite_integrity(self, path: pathlib.Path) -> tuple[bool, str]:
        """Run SQLite integrity_check without blocking the event loop."""
        try:
            return await run_blocking_io(self._check_sqlite_integrity_sync, path)
        except Exception as exc:
            return False, str(exc)

    async def verify_database_backup(
        self,
        name: str,
        *,
        lock: bool = True,
    ) -> tuple[bool, str]:
        """Verify a managed backup archive or legacy database snapshot."""
        if lock:
            async with database_file_lock(self):
                return await self.verify_database_backup(name, lock=False)

        backup = self.resolve_database_backup(name)
        if backup is None:
            return False, f"Backup not found: {name}"

        lines = [f"🔎 Backup verification: {backup.name}"]
        if self._is_backup_archive(backup.path):
            lines.append("Format: ZIP archive")
            with tempfile.TemporaryDirectory(prefix="banbot-backup-verify-") as tmp_name:
                tmp_dir = pathlib.Path(tmp_name)
                try:
                    sources = await self._extract_backup_archive(
                        backup.path, tmp_dir, verify_checksums=False
                    )
                except Exception as exc:
                    lines.append(f"❌ Backup archive check failed: {exc}")
                    return False, "\n".join(lines)

                database_source = sources.get("database")
                if database_source is None:
                    lines.append("❌ Backup archive check failed: database.sqlite3 is missing")
                    return False, "\n".join(lines)
                ok, message = await self._check_sqlite_integrity(database_source)
                if ok:
                    lines.append("✅ SQLite integrity_check: ok")
                else:
                    lines.append(f"❌ SQLite integrity_check failed: {message}")
                    return False, "\n".join(lines)

                config_source = sources.get("config")
                if config_source is not None:
                    try:
                        text = await run_blocking_io(config_source.read_text, encoding="utf-8")
                        compile(text, str(config_source), "exec")
                        lines.append("✅ config.py companion: readable and valid Python")
                    except Exception as exc:
                        lines.append(f"❌ config.py companion check failed: {exc}")
                        return False, "\n".join(lines)
                else:
                    lines.append("ℹ️ config.py companion: not present")

                omemo_source = sources.get("omemo")
                if omemo_source is not None:
                    try:
                        text = await run_blocking_io(omemo_source.read_text, encoding="utf-8")
                        if text.strip():
                            json.loads(text)
                        lines.append("✅ OMEMO companion: readable JSON")
                    except Exception as exc:
                        lines.append(f"❌ OMEMO companion check failed: {exc}")
                        return False, "\n".join(lines)
                else:
                    lines.append("ℹ️ OMEMO companion: not present")

                archive_verification = await run_blocking_io(
                    verify_backup_archive,
                    backup.path,
                    manifest_name=_BACKUP_MANIFEST_ENTRY,
                    expected_fields={"format": _BACKUP_FORMAT},
                    required_members=[_BACKUP_DATABASE_ENTRY],
                    allow_legacy_without_files=True,
                )
                if not archive_verification.ok:
                    lines.append(
                        "❌ Backup archive checksum check failed: "
                        + "; ".join(archive_verification.errors)
                    )
                    return False, "\n".join(lines)
                if archive_verification.files:
                    lines.append("✅ Archive manifest/checksums: ok")
            return True, "\n".join(lines)

        ok, message = await self._check_sqlite_integrity(backup.path)
        if ok:
            lines.append("✅ SQLite integrity_check: ok")
        else:
            lines.append(f"❌ SQLite integrity_check failed: {message}")
            return False, "\n".join(lines)

        config_backup = self._config_backup_path_for(backup.path)
        if config_backup.is_file():
            try:
                text = await run_blocking_io(config_backup.read_text, encoding="utf-8")
                compile(text, str(config_backup), "exec")
                lines.append("✅ config.py companion: readable and valid Python")
            except Exception as exc:
                lines.append(f"❌ config.py companion check failed: {exc}")
                return False, "\n".join(lines)
        else:
            lines.append("ℹ️ config.py companion: not present")

        omemo_backup = self._omemo_backup_path_for(backup.path)
        if omemo_backup.is_file():
            try:
                text = await run_blocking_io(omemo_backup.read_text, encoding="utf-8")
                if text.strip():
                    json.loads(text)
                lines.append("✅ OMEMO companion: readable JSON")
            except Exception as exc:
                lines.append(f"❌ OMEMO companion check failed: {exc}")
                return False, "\n".join(lines)
        else:
            lines.append("ℹ️ OMEMO companion: not present")

        return True, "\n".join(lines)
