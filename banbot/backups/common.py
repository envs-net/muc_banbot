"""Shared helpers and constants for managed backup handling."""

from __future__ import annotations

import re

from ..managed_files import ManagedFile

_BACKUP_SAFE_RE = re.compile(r"[^A-Za-z0-9_.-]+")

_BACKUP_FORMAT = "banbot-backup-v1"
_BACKUP_MANIFEST_ENTRY = "manifest.json"
_BACKUP_DATABASE_ENTRY = "database.sqlite3"
_BACKUP_CONFIG_ENTRY = "config.py"
_BACKUP_OMEMO_ENTRY = "omemo.json"

DatabaseBackup = ManagedFile
