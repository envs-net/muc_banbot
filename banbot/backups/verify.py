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

class BackupVerifyMixin:

    @staticmethod
    def _check_sqlite_integrity_sync(path: pathlib.Path) -> tuple[bool, str]:
        """Run SQLite PRAGMA integrity_check for a database file."""
        if not path.exists():
            return False, f"Database file does not exist: {path}"
        if not path.is_file():
            return False, f"Database path is not a regular file: {path}"
        if path.stat().st_size <= 0:
            return False, f"Database file is empty: {path}"

        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            result = connection.execute("PRAGMA integrity_check").fetchone()
        finally:
            connection.close()

        message = str(result[0]) if result else "no integrity_check result"
        return message.lower() == "ok", message

    async def _check_sqlite_integrity(self, path: pathlib.Path) -> tuple[bool, str]:
        """Run SQLite integrity_check without blocking the event loop."""
        try:
            return await asyncio.to_thread(self._check_sqlite_integrity_sync, path)
        except Exception as exc:
            return False, str(exc)

    async def verify_database_backup(self, name: str) -> tuple[bool, str]:
        """Verify a managed backup archive or legacy database snapshot."""
        backup = self.resolve_database_backup(name)
        if backup is None:
            return False, f"Backup not found: {name}"

        lines = [f"🔎 Backup verification: {backup.name}"]
        if self._is_backup_archive(backup.path):
            lines.append("Format: ZIP archive")
            with tempfile.TemporaryDirectory(prefix="banbot-backup-verify-") as tmp_name:
                tmp_dir = pathlib.Path(tmp_name)
                try:
                    sources = await self._extract_backup_archive(backup.path, tmp_dir)
                except Exception as exc:
                    lines.append(f"❌ Backup archive check failed: {exc}")
                    return False, "\n".join(lines)

                database_source = sources.get("database")
                ok, message = await self._check_sqlite_integrity(database_source)  # type: ignore[arg-type]
                if ok:
                    lines.append("✅ SQLite integrity_check: ok")
                else:
                    lines.append(f"❌ SQLite integrity_check failed: {message}")
                    return False, "\n".join(lines)

                config_source = sources.get("config")
                if config_source is not None:
                    try:
                        text = await asyncio.to_thread(config_source.read_text, encoding="utf-8")
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
                        text = await asyncio.to_thread(omemo_source.read_text, encoding="utf-8")
                        if text.strip():
                            json.loads(text)
                        lines.append("✅ OMEMO companion: readable JSON")
                    except Exception as exc:
                        lines.append(f"❌ OMEMO companion check failed: {exc}")
                        return False, "\n".join(lines)
                else:
                    lines.append("ℹ️ OMEMO companion: not present")
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
                text = await asyncio.to_thread(config_backup.read_text, encoding="utf-8")
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
                text = await asyncio.to_thread(omemo_backup.read_text, encoding="utf-8")
                if text.strip():
                    json.loads(text)
                lines.append("✅ OMEMO companion: readable JSON")
            except Exception as exc:
                lines.append(f"❌ OMEMO companion check failed: {exc}")
                return False, "\n".join(lines)
        else:
            lines.append("ℹ️ OMEMO companion: not present")

        return True, "\n".join(lines)
