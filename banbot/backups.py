"""SQLite database backup, restore and startup snapshot helpers."""

from __future__ import annotations

import asyncio
import logging
import os
import pathlib
import re
import shutil
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from typing import Any

try:
    import config
except ModuleNotFoundError:
    config = None

log = logging.getLogger(__name__)

from .locks import get_ban_state_lock, get_database_file_lock

_BACKUP_SAFE_RE = re.compile(r"[^A-Za-z0-9_.-]+")


@dataclass(frozen=True)
class DatabaseBackup:
    """Metadata for one managed database backup file."""

    path: pathlib.Path
    size: int
    mtime: float

    @property
    def name(self) -> str:
        return self.path.name

    @property
    def mtime_text(self) -> str:
        return datetime.fromtimestamp(self.mtime).strftime("%Y-%m-%d %H:%M:%S")


class BackupMixin:
    """Create, list and restore managed SQLite database/config/OMEMO backups."""

    last_database_backup_file: str | None = None
    last_database_restore_file: str | None = None

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
        """Return the companion config.py backup path for a database snapshot."""
        return backup_path.with_name(f"{backup_path.name}.config.py")

    def _omemo_backup_path_for(self, backup_path: pathlib.Path) -> pathlib.Path:
        """Return the companion OMEMO storage backup path for a database snapshot."""
        return backup_path.with_name(f"{backup_path.name}.omemo.json")

    def _has_config_backup(self, backup_path: pathlib.Path) -> bool:
        return self._config_backup_path_for(backup_path).is_file()

    def _has_omemo_backup(self, backup_path: pathlib.Path) -> bool:
        return self._omemo_backup_path_for(backup_path).is_file()

    def _is_backup_supported_database(self, db_path: pathlib.Path | None = None) -> bool:
        path = db_path or self._database_path()
        return str(path) not in ("", ":memory:")

    def _safe_backup_reason(self, reason: str) -> str:
        cleaned = _BACKUP_SAFE_RE.sub("-", str(reason).strip().lower()).strip("-._")
        return cleaned or "manual"

    def _format_backup_size(self, size: int) -> str:
        if size >= 1024 * 1024:
            return f"{size / (1024 * 1024):.1f} MiB"
        if size >= 1024:
            return f"{size / 1024:.1f} KiB"
        return f"{size} B"

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

    def list_database_backups(self) -> list[DatabaseBackup]:
        """Return managed backup files sorted newest first."""
        backup_dir = self._database_backup_dir()
        if not backup_dir.exists():
            return []

        backups: list[DatabaseBackup] = []
        for path in backup_dir.glob(self._database_backup_pattern()):
            if not path.is_file() or path.name.endswith((".config.py", ".omemo.json")):
                continue
            try:
                stat = path.stat()
            except OSError:
                continue
            backups.append(DatabaseBackup(path=path, size=stat.st_size, mtime=stat.st_mtime))

        backups.sort(key=lambda item: (item.mtime, item.name), reverse=True)
        return backups

    async def prune_database_backups(self, *, preserve: pathlib.Path | None = None) -> list[pathlib.Path]:
        """Delete old managed backups beyond DB_BACKUP_KEEP."""
        keep = self._database_backup_keep()
        preserve_resolved = preserve.resolve() if preserve is not None and preserve.exists() else None
        removed: list[pathlib.Path] = []
        backups = self.list_database_backups()

        kept = 0
        for backup in backups:
            try:
                backup_resolved = backup.path.resolve()
            except OSError:
                backup_resolved = backup.path

            if preserve_resolved is not None and backup_resolved == preserve_resolved:
                kept += 1
                continue

            if kept < keep:
                kept += 1
                continue

            try:
                backup.path.unlink()
                removed.append(backup.path)
                for companion in (
                    self._config_backup_path_for(backup.path),
                    self._omemo_backup_path_for(backup.path),
                ):
                    if companion.exists():
                        companion.unlink()
                        removed.append(companion)
                log.info("Deleted old database backup: %s", backup.path)
            except OSError as exc:
                log.warning("Failed to delete old database backup %s: %s", backup.path, exc)

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
        """Create a timestamped database backup and optionally prune old backups."""
        if lock:
            async with get_database_file_lock(self):
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
        backup_path = backup_dir / base_name
        counter = 1
        while backup_path.exists():
            backup_path = backup_dir / f"{base_name}-{counter}"
            counter += 1

        try:
            await self._copy_database_to_backup(db_path, backup_path)
            try:
                os.chmod(backup_path, 0o600)
            except OSError as exc:
                log.debug("Failed to restrict database backup permissions for %s: %s", backup_path, exc)

            config_path = self._config_path()
            config_backup_path: pathlib.Path | None = None
            if config_path is not None and config_path.exists() and config_path.is_file():
                config_backup_path = self._config_backup_path_for(backup_path)
                await asyncio.to_thread(shutil.copy2, config_path, config_backup_path)
                try:
                    os.chmod(config_backup_path, 0o600)
                except OSError as exc:
                    log.debug("Failed to restrict config backup permissions for %s: %s", config_backup_path, exc)

            omemo_path = self._omemo_storage_path()
            omemo_backup_path: pathlib.Path | None = None
            if self._database_backup_include_omemo():
                if omemo_path is not None and omemo_path.exists() and omemo_path.is_file():
                    omemo_backup_path = self._omemo_backup_path_for(backup_path)
                    await asyncio.to_thread(shutil.copy2, omemo_path, omemo_backup_path)
                    try:
                        os.chmod(omemo_backup_path, 0o600)
                    except OSError as exc:
                        log.debug("Failed to restrict OMEMO backup permissions for %s: %s", omemo_backup_path, exc)
                else:
                    log.debug("OMEMO backup skipped: storage file does not exist or is not configured")

            self.last_database_backup_file = str(backup_path)
            if prune:
                await self.prune_database_backups(preserve=backup_path)

            log.info("Created database backup: %s", backup_path)
            if hasattr(self, "log_event"):
                try:
                    self.log_event(
                        logging.INFO,
                        "db_backup_created",
                        actor=actor or "system",
                        backup=str(backup_path),
                        config_backup=str(config_backup_path) if config_backup_path else None,
                        omemo_backup=str(omemo_backup_path) if omemo_backup_path else None,
                        reason=reason_slug,
                    )
                except Exception as exc:
                    log.debug("Failed to write backup structured event: %s", exc)
            audit_actor = actor or "system"
            audit_details = {
                "backup": str(backup_path),
                "config_backup": str(config_backup_path) if config_backup_path else None,
                "omemo_backup": str(omemo_backup_path) if omemo_backup_path else None,
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

    def resolve_database_backup(self, name: str) -> DatabaseBackup | None:
        """Resolve a backup by basename, path inside backup dir, or 'latest'."""
        query = str(name).strip()
        backups = self.list_database_backups()
        if not query or not backups:
            return None
        if query.lower() == "latest":
            return backups[0]

        backup_dir = self._database_backup_dir().resolve()
        for backup in backups:
            if query == backup.name or query == str(backup.path):
                return backup

        # Allow paths only if they still resolve inside the configured backup dir.
        candidate = pathlib.Path(query).expanduser()
        if not candidate.is_absolute():
            candidate = self._database_backup_dir() / candidate
        try:
            resolved = candidate.resolve()
        except OSError:
            return None
        if backup_dir not in (resolved, *resolved.parents):
            return None
        if not resolved.is_file() or resolved.name.endswith((".config.py", ".omemo.json")):
            return None
        stat = resolved.stat()
        return DatabaseBackup(path=resolved, size=stat.st_size, mtime=stat.st_mtime)

    def _format_backup_details(self, backup: DatabaseBackup) -> str:
        companions = self._backup_companion_names(backup.path)
        lines = [
            f"💾 Backup: {backup.name}",
            f"Path: {backup.path}",
            f"Size: {self._format_backup_size(backup.size)}",
            f"Modified: {backup.mtime_text}",
            "Companions: " + (", ".join(companions) if companions else "none"),
        ]
        return "\n".join(lines)

    async def restore_database_backup(self, name: str, *, actor: str | None = None) -> tuple[bool, str]:
        """Restore a managed database backup and reload DB-backed caches."""
        async with get_database_file_lock(self):
            async with get_ban_state_lock(self):
                return await self._restore_database_backup_locked(name, actor=actor)

    async def _restore_database_backup_locked(self, name: str, *, actor: str | None = None) -> tuple[bool, str]:
        """Restore implementation. The requested backup is resolved before the safety backup."""
        backup = self.resolve_database_backup(name)
        if backup is None:
            return False, f"Backup not found: {name}"

        db_path = self._database_path()
        if not self._is_backup_supported_database(db_path):
            return False, "Database restore is not available for in-memory DB_FILE."

        db_path.parent.mkdir(parents=True, exist_ok=True)
        safety_ok, safety_message = await self.create_database_backup(
            "before-restore",
            prune=False,
            actor=actor or "unknown",
            lock=False,
        )
        if not safety_ok and db_path.exists():
            return False, f"Restore aborted: failed to create safety backup: {safety_message}"

        try:
            if getattr(self, "db", None):
                await self.db.commit()
                await self.db.close()
                self.db = None

            await asyncio.to_thread(shutil.copy2, backup.path, db_path)
            try:
                os.chmod(db_path, 0o600)
            except OSError as exc:
                log.debug("Failed to restrict restored database permissions for %s: %s", db_path, exc)

            restored_config = False
            config_backup_path = self._config_backup_path_for(backup.path)
            config_path = self._config_path()
            if config_backup_path.is_file() and config_path is not None:
                config_path.parent.mkdir(parents=True, exist_ok=True)
                await asyncio.to_thread(shutil.copy2, config_backup_path, config_path)
                try:
                    os.chmod(config_path, 0o600)
                except OSError as exc:
                    log.debug("Failed to restrict restored config permissions for %s: %s", config_path, exc)
                restored_config = True

            restored_omemo = False
            omemo_backup_path = self._omemo_backup_path_for(backup.path)
            omemo_path = self._omemo_storage_path()
            if omemo_backup_path.is_file() and omemo_path is not None:
                omemo_path.parent.mkdir(parents=True, exist_ok=True)
                try:
                    os.chmod(omemo_path.parent, 0o700)
                except OSError as exc:
                    log.debug("Failed to restrict OMEMO storage directory permissions for %s: %s", omemo_path.parent, exc)
                await asyncio.to_thread(shutil.copy2, omemo_backup_path, omemo_path)
                try:
                    os.chmod(omemo_path, 0o600)
                except OSError as exc:
                    log.debug("Failed to restrict restored OMEMO storage permissions for %s: %s", omemo_path, exc)
                restored_omemo = True

            # Re-open and reload the most important DB-backed runtime state when
            # the full bot mixin stack is available. Lightweight tests may only
            # exercise the file operation and skip these optional hooks.
            if hasattr(self, "protected_rooms") and isinstance(self.protected_rooms, set):
                self.protected_rooms.clear()
            if hasattr(self, "setup_db"):
                try:
                    await self.setup_db(create_startup_backup=False)
                except TypeError:
                    await self.setup_db()
            if hasattr(self, "load_bans_from_db"):
                await self.load_bans_from_db()
            if hasattr(self, "_load_ignorelist_from_db"):
                await self._load_ignorelist_from_db()
            elif hasattr(self, "setup_ignorelist"):
                await self.setup_ignorelist()
            if hasattr(self, "_load_rtbl_subscriptions_from_db") and getattr(self, "rtbl_enabled", False):
                await self._load_rtbl_subscriptions_from_db()
            if hasattr(self, "load_pending_room_invites"):
                await self.load_pending_room_invites()

            self.last_database_restore_file = str(backup.path)
            await self.prune_database_backups(preserve=backup.path)

            if hasattr(self, "log_event"):
                try:
                    self.log_event(
                        logging.INFO,
                        "db_backup_restored",
                        actor=actor,
                        backup=str(backup.path),
                        safety_backup=safety_message if safety_ok else None,
                        config_restored=restored_config,
                        omemo_restored=restored_omemo,
                    )
                except Exception as exc:
                    log.debug("Failed to write restore structured event: %s", exc)
            if hasattr(self, "audit_event"):
                try:
                    await self.audit_event(
                        "db_backup_restored",
                        actor=actor or "unknown",
                        target_type="backup",
                        target=backup.name,
                        details={
                            "backup": str(backup.path),
                            "safety_backup": safety_message if safety_ok else None,
                            "config_restored": restored_config,
                            "omemo_restored": restored_omemo,
                        },
                    )
                except Exception as exc:
                    log.debug("Failed to audit database restore: %s", exc)

            lines = [f"✅ Database restored from {backup.name}"]
            if restored_config:
                lines.append("config.py was restored from the backup companion file.")
                lines.append("⚠️ Run !reloadconfig or restart the bot to apply restored config.py values.")
            elif config_backup_path.is_file():
                lines.append("config.py backup companion exists, but no active config.py path was available for restore.")
            else:
                lines.append("No config.py companion file was found for this backup.")
            if restored_omemo:
                lines.append("OMEMO storage was restored from the backup companion file.")
                lines.append("⚠️ Restart the bot before using restored OMEMO sessions.")
            elif omemo_backup_path.is_file():
                lines.append("OMEMO backup companion exists, but no OMEMO_STORAGE_FILE path was available for restore.")
            else:
                lines.append("No OMEMO companion file was found for this backup.")
            if safety_ok:
                lines.append(f"Safety backup before restore: {safety_message}")
            return True, "\n".join(lines)
        except Exception as exc:
            log.error("Failed to restore database backup %s: %s", backup.path, exc)
            return False, str(exc)

    async def cmd_backup(self, args: list[str], room: str, actor: str | None = None) -> None:
        """Handle !backup commands."""
        args = args or []
        action = args[0].lower() if args else "create"

        if action in ("create", "now"):
            ok, message = await self.create_database_backup("manual", actor=actor or "unknown")
            if ok:
                companions = self._backup_companion_names(pathlib.Path(message))
                companion_note = f"\nIncluded companions: {', '.join(companions)}" if companions else "\nIncluded companions: none"
                body = f"✅ Full backup created (Database/config backup created):\n{message}{companion_note}"
            else:
                body = f"❌ Database backup failed: {message}"
            await self.bot_send_message(mto=room, mbody=body, mtype="groupchat")
            return

        if action == "list":
            backups = self.list_database_backups()
            lines = ["💾 Managed Full Backups"]
            lines.append(f"Directory: {self._database_backup_dir()}")
            lines.append(f"Keep: {self._database_backup_keep()}")
            if not backups:
                lines.append("\nNo managed database backups found.")
            else:
                lines.append("")
                for index, backup in enumerate(backups[:20], start=1):
                    lines.append(self._format_backup_entry(backup, index))
                if len(backups) > 20:
                    lines.append(f"... and {len(backups) - 20} more backups")
                lines.append("")
                lines.append(f"Restore with: {self.command_prefix}restore <filename|latest> confirm")
            await self.bot_send_message(mto=room, mbody="\n".join(lines), mtype="groupchat")
            return

        if action == "show":
            if len(args) < 2:
                await self.bot_send_message(
                    mto=room,
                    mbody=f"❌ Usage: {self.command_prefix}backup show <filename|latest>",
                    mtype="groupchat",
                )
                return
            backup = self.resolve_database_backup(args[1])
            if backup is None:
                await self.bot_send_message(mto=room, mbody=f"❌ Backup not found: {args[1]}", mtype="groupchat")
                return
            await self.bot_send_message(mto=room, mbody=self._format_backup_details(backup), mtype="groupchat")
            return

        if action == "restore":
            await self.cmd_restore(args[1:], room, actor=actor)
            return

        await self.bot_send_message(
            mto=room,
            mbody=(
                "Usage:\n"
                f"  {self.command_prefix}backup\n"
                f"  {self.command_prefix}backup list\n"
                f"  {self.command_prefix}backup show <filename|latest>\n"
                f"  {self.command_prefix}backup restore <filename|latest> confirm\n"
                f"  {self.command_prefix}restore <filename|latest> confirm"
            ),
            mtype="groupchat",
        )

    async def cmd_restore(self, args: list[str], room: str, actor: str | None = None) -> None:
        """Handle !restore and !backup restore."""
        if len(args) < 1:
            await self.bot_send_message(
                mto=room,
                mbody=f"❌ Usage: {self.command_prefix}restore <filename|latest> confirm",
                mtype="groupchat",
            )
            return

        backup_name = args[0]
        backup = self.resolve_database_backup(backup_name)
        if backup is None:
            await self.bot_send_message(mto=room, mbody=f"❌ Backup not found: {backup_name}", mtype="groupchat")
            return

        if len(args) < 2 or args[1].lower() != "confirm":
            await self.bot_send_message(
                mto=room,
                mbody=(
                    "⚠️ This will replace the current SQLite database"
                    " and restore config.py/OMEMO companions when present.\n\n"
                    f"Backup selected:\n{self._format_backup_entry(backup)}\n\n"
                    "A safety backup of the current DB will be created first.\n"
                    f"Confirm with: {self.command_prefix}restore {backup.name} confirm"
                ),
                mtype="groupchat",
            )
            return

        ok, message = await self.restore_database_backup(backup.name, actor=actor)
        prefix = "✅ Restore complete." if ok else "❌ Restore failed."
        await self.bot_send_message(mto=room, mbody=f"{prefix}\n{message}", mtype="groupchat")
