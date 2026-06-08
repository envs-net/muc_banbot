"""Managed backup restore helpers."""

from __future__ import annotations

import asyncio
import logging
import os
import pathlib
import shutil
import tempfile

from ..locks import database_mutation_locks

log = logging.getLogger(__name__)

class BackupRestoreMixin:

    async def restore_database_backup(self, name: str, *, actor: str | None = None) -> tuple[bool, str]:
        """Restore a managed database backup and reload DB-backed caches."""
        async with database_mutation_locks(self):
            return await self._restore_database_backup_locked(name, actor=actor)

    async def _restore_database_backup_locked(self, name: str, *, actor: str | None = None) -> tuple[bool, str]:
        """Restore implementation. The requested backup is resolved before the safety backup."""
        backup = self.resolve_database_backup(name)
        if backup is None:
            return False, f"Backup not found: {name}"

        db_path = self._database_path()
        if not self._is_backup_supported_database(db_path):
            return False, "Database restore is not available for in-memory DB_FILE."

        with tempfile.TemporaryDirectory(prefix="banbot-backup-restore-") as tmp_name:
            tmp_dir = pathlib.Path(tmp_name)
            try:
                sources = await self._backup_restore_sources(backup.path, tmp_dir)
            except Exception as exc:
                return False, f"Restore aborted: selected backup could not be prepared: {exc}"

            database_source = sources.get("database")
            verify_ok, verify_message = await self._check_sqlite_integrity(database_source)  # type: ignore[arg-type]
            if not verify_ok:
                return False, f"Restore aborted: selected backup failed SQLite integrity_check: {verify_message}"

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

                await asyncio.to_thread(shutil.copy2, database_source, db_path)  # type: ignore[arg-type]
                try:
                    os.chmod(db_path, 0o600)
                except OSError as exc:
                    log.debug("Failed to restrict restored database permissions for %s: %s", db_path, exc)

                restored_ok, restored_message = await self._check_sqlite_integrity(db_path)
                if not restored_ok:
                    rollback_note = ""
                    if safety_ok:
                        try:
                            with tempfile.TemporaryDirectory(prefix="banbot-backup-rollback-") as rollback_tmp_name:
                                rollback_sources = await self._backup_restore_sources(
                                    pathlib.Path(safety_message),
                                    pathlib.Path(rollback_tmp_name),
                                )
                                await asyncio.to_thread(shutil.copy2, rollback_sources["database"], db_path)
                            rollback_note = f" Safety backup was restored: {safety_message}"
                        except Exception as rollback_exc:
                            rollback_note = f" Safety backup restore failed: {rollback_exc}"
                    return False, f"Restore aborted: restored DB failed integrity_check: {restored_message}.{rollback_note}"

                restored_config = False
                config_source = sources.get("config")
                config_path = self._config_path()
                if config_source is not None and config_path is not None:
                    config_path.parent.mkdir(parents=True, exist_ok=True)
                    await asyncio.to_thread(shutil.copy2, config_source, config_path)
                    try:
                        os.chmod(config_path, 0o600)
                    except OSError as exc:
                        log.debug("Failed to restrict restored config permissions for %s: %s", config_path, exc)
                    restored_config = True

                restored_omemo = False
                omemo_source = sources.get("omemo")
                omemo_path = self._omemo_storage_path()
                if omemo_source is not None and omemo_path is not None:
                    omemo_path.parent.mkdir(parents=True, exist_ok=True)
                    try:
                        os.chmod(omemo_path.parent, 0o700)
                    except OSError as exc:
                        log.debug("Failed to restrict OMEMO storage directory permissions for %s: %s", omemo_path.parent, exc)
                    await asyncio.to_thread(shutil.copy2, omemo_source, omemo_path)
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
                command_prefix = getattr(self, "command_prefix", "!")
                if restored_config:
                    lines.append("config.py was restored from the backup archive.")
                    lines.append(f"⚠️ Run {command_prefix}reloadconfig or restart the bot to apply restored config.py values.")
                elif config_source is not None:
                    lines.append("config.py backup companion exists, but no active config.py path was available for restore.")
                else:
                    lines.append("No config.py companion file was found for this backup.")
                if restored_omemo:
                    lines.append("OMEMO storage was restored from the backup archive.")
                    lines.append("⚠️ Restart the bot before using restored OMEMO sessions.")
                elif omemo_source is not None:
                    lines.append("OMEMO backup companion exists, but no OMEMO_STORAGE_FILE path was available for restore.")
                else:
                    lines.append("No OMEMO companion file was found for this backup.")
                if safety_ok:
                    lines.append(f"Safety backup before restore: {safety_message}")
                return True, "\n".join(lines)
            except Exception as exc:
                log.error("Failed to restore database backup %s: %s", backup.path, exc)
                return False, str(exc)

    async def delete_database_backup(self, name: str, *, actor: str | None = None) -> tuple[bool, str]:
        """Delete a managed database backup archive and any legacy companion files."""
        backup = self.resolve_database_backup(name)
        if backup is None:
            return False, f"Backup not found: {name}"

        companion_paths = [
            self._config_backup_path_for(backup.path),
            self._omemo_backup_path_for(backup.path),
        ]
        removed: list[str] = []

        try:
            for path in [backup.path, *companion_paths]:
                if path.exists():
                    await asyncio.to_thread(path.unlink)
                    removed.append(path.name)
        except OSError as exc:
            log.warning("Failed to delete backup %s: %s", backup.path, exc)
            return False, str(exc)

        if hasattr(self, "log_event"):
            try:
                self.log_event(
                    logging.INFO,
                    "db_backup_deleted",
                    actor=actor or "unknown",
                    backup=str(backup.path),
                    removed=removed,
                )
            except Exception as exc:
                log.debug("Failed to write backup delete structured event: %s", exc)

        if hasattr(self, "audit_event") and getattr(self, "db", None):
            try:
                await self.audit_event(
                    "db_backup_deleted",
                    actor=actor or "unknown",
                    target_type="backup",
                    target=backup.name,
                    details={"backup": str(backup.path), "removed": removed},
                )
            except Exception as exc:
                log.debug("Failed to audit backup deletion: %s", exc)

        return True, "Deleted files: " + (", ".join(removed) if removed else backup.name)
