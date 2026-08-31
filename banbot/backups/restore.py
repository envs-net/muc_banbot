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

    async def _reload_database_runtime_after_restore(self) -> None:
        """Open the current DB file and refresh DB-backed runtime state."""
        room_set = getattr(self, "protected_rooms", None)
        previous_rooms = set(room_set) if isinstance(room_set, set) else None
        if isinstance(room_set, set):
            room_set.clear()

        try:
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
        except Exception:
            if isinstance(room_set, set) and previous_rooms is not None:
                room_set.clear()
                room_set.update(previous_rooms)
            raise

    async def _rollback_failed_restore(
        self,
        *,
        db_path: pathlib.Path,
        db_existed_before: bool,
        safety_backup: pathlib.Path | None,
        config_path: pathlib.Path | None,
        config_modified: bool,
        config_existed_before: bool,
        config_snapshot: pathlib.Path | None,
        omemo_path: pathlib.Path | None,
        omemo_modified: bool,
        omemo_existed_before: bool,
        omemo_snapshot: pathlib.Path | None,
    ) -> str:
        """Best-effort rollback of every file touched by a failed restore."""
        errors: list[str] = []

        db = getattr(self, "db", None)
        if db is not None:
            try:
                await db.close()
            except Exception as exc:
                errors.append(f"close current DB: {exc}")
            finally:
                self.db = None

        try:
            if db_existed_before:
                if safety_backup is None:
                    raise RuntimeError("no safety backup is available for the previous database")
                with tempfile.TemporaryDirectory(prefix="banbot-backup-rollback-") as rollback_tmp_name:
                    rollback_sources = await self._backup_restore_sources(
                        safety_backup,
                        pathlib.Path(rollback_tmp_name),
                    )
                    database_source = rollback_sources.get("database")
                    if database_source is None:
                        raise RuntimeError("safety backup contains no database")
                    await asyncio.to_thread(shutil.copy2, database_source, db_path)
                try:
                    os.chmod(db_path, 0o600)
                except OSError as exc:
                    log.debug("Failed to restrict rolled-back database permissions for %s: %s", db_path, exc)
            elif db_path.exists():
                await asyncio.to_thread(db_path.unlink)
        except Exception as exc:
            errors.append(f"database rollback: {exc}")

        async def restore_optional_file(
            label: str,
            path: pathlib.Path | None,
            *,
            modified: bool,
            existed_before: bool,
            snapshot: pathlib.Path | None,
            mode: int,
        ) -> None:
            if not modified or path is None:
                return
            try:
                if existed_before:
                    if snapshot is None or not snapshot.is_file():
                        raise RuntimeError("pre-restore snapshot is unavailable")
                    path.parent.mkdir(parents=True, exist_ok=True)
                    await asyncio.to_thread(shutil.copy2, snapshot, path)
                    try:
                        os.chmod(path, mode)
                    except OSError as exc:
                        log.debug("Failed to restrict rolled-back %s permissions for %s: %s", label, path, exc)
                elif path.exists():
                    await asyncio.to_thread(path.unlink)
            except Exception as exc:
                errors.append(f"{label} rollback: {exc}")

        await restore_optional_file(
            "config",
            config_path,
            modified=config_modified,
            existed_before=config_existed_before,
            snapshot=config_snapshot,
            mode=0o600,
        )
        await restore_optional_file(
            "OMEMO",
            omemo_path,
            modified=omemo_modified,
            existed_before=omemo_existed_before,
            snapshot=omemo_snapshot,
            mode=0o600,
        )

        try:
            await self._reload_database_runtime_after_restore()
        except Exception as exc:
            errors.append(f"runtime recovery: {exc}")
            db = getattr(self, "db", None)
            if db is not None:
                try:
                    await db.close()
                except Exception as close_exc:
                    log.debug("Failed to close unusable DB connection after rollback: %s", close_exc)
                finally:
                    self.db = None

        if errors:
            log.error("Restore rollback was incomplete: %s", "; ".join(errors))
            return " Rollback incomplete: " + "; ".join(errors)
        return " Previous files were restored. Database connection/runtime state was recovered."

    async def restore_database_backup(self, name: str, *, actor: str | None = None) -> tuple[bool, str]:
        """Restore a managed database backup and reload DB-backed caches."""
        async with database_mutation_locks(self):
            return await self._restore_database_backup_locked(name, actor=actor)

    async def _restore_database_backup_locked(self, name: str, *, actor: str | None = None) -> tuple[bool, str]:
        """Restore implementation with all-file rollback on any post-swap failure."""
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

            db_existed_before = db_path.exists()
            db_path.parent.mkdir(parents=True, exist_ok=True)
            safety_ok, safety_message = await self.create_database_backup(
                "before-restore",
                prune=False,
                actor=actor or "unknown",
                lock=False,
            )
            if not safety_ok and db_existed_before:
                return False, f"Restore aborted: failed to create safety backup: {safety_message}"
            safety_backup = pathlib.Path(safety_message) if safety_ok else None

            config_source = sources.get("config")
            config_path = self._config_path()
            config_will_restore = config_source is not None and config_path is not None
            config_existed_before = bool(config_path is not None and config_path.is_file())
            config_snapshot: pathlib.Path | None = None

            omemo_source = sources.get("omemo")
            omemo_path = self._omemo_storage_path()
            omemo_will_restore = omemo_source is not None and omemo_path is not None
            omemo_existed_before = bool(omemo_path is not None and omemo_path.is_file())
            omemo_snapshot: pathlib.Path | None = None

            # Capture companions independently from DB_BACKUP_INCLUDE_OMEMO so a
            # failed restore can always undo files that this operation modifies.
            try:
                if config_will_restore and config_existed_before and config_path is not None:
                    config_snapshot = tmp_dir / "pre-restore-config.py"
                    await asyncio.to_thread(shutil.copy2, config_path, config_snapshot)
                if omemo_will_restore and omemo_existed_before and omemo_path is not None:
                    omemo_snapshot = tmp_dir / "pre-restore-omemo.json"
                    await asyncio.to_thread(shutil.copy2, omemo_path, omemo_snapshot)
            except Exception as exc:
                return False, f"Restore aborted: failed to snapshot current companion files: {exc}"

            restore_started = False
            config_modified = False
            omemo_modified = False
            try:
                if getattr(self, "db", None):
                    await self.db.commit()
                    await self.db.close()
                    self.db = None

                restore_started = True
                await asyncio.to_thread(shutil.copy2, database_source, db_path)  # type: ignore[arg-type]
                try:
                    os.chmod(db_path, 0o600)
                except OSError as exc:
                    log.debug("Failed to restrict restored database permissions for %s: %s", db_path, exc)

                restored_ok, restored_message = await self._check_sqlite_integrity(db_path)
                if not restored_ok:
                    rollback_note = await self._rollback_failed_restore(
                        db_path=db_path,
                        db_existed_before=db_existed_before,
                        safety_backup=safety_backup,
                        config_path=config_path,
                        config_modified=config_modified,
                        config_existed_before=config_existed_before,
                        config_snapshot=config_snapshot,
                        omemo_path=omemo_path,
                        omemo_modified=omemo_modified,
                        omemo_existed_before=omemo_existed_before,
                        omemo_snapshot=omemo_snapshot,
                    )
                    return (
                        False,
                        f"Restore aborted: restored DB failed integrity_check: "
                        f"{restored_message}.{rollback_note}",
                    )

                restored_config = False
                if config_will_restore and config_source is not None and config_path is not None:
                    config_path.parent.mkdir(parents=True, exist_ok=True)
                    config_modified = True
                    await asyncio.to_thread(shutil.copy2, config_source, config_path)
                    try:
                        os.chmod(config_path, 0o600)
                    except OSError as exc:
                        log.debug("Failed to restrict restored config permissions for %s: %s", config_path, exc)
                    restored_config = True

                restored_omemo = False
                if omemo_will_restore and omemo_source is not None and omemo_path is not None:
                    omemo_path.parent.mkdir(parents=True, exist_ok=True)
                    try:
                        os.chmod(omemo_path.parent, 0o700)
                    except OSError as exc:
                        log.debug(
                            "Failed to restrict OMEMO storage directory permissions for %s: %s",
                            omemo_path.parent,
                            exc,
                        )
                    omemo_modified = True
                    await asyncio.to_thread(shutil.copy2, omemo_source, omemo_path)
                    try:
                        os.chmod(omemo_path, 0o600)
                    except OSError as exc:
                        log.debug("Failed to restrict restored OMEMO storage permissions for %s: %s", omemo_path, exc)
                    restored_omemo = True

                # Re-open and reload DB-backed runtime state after every file
                # swap. Any failure below triggers an all-file rollback.
                await self._reload_database_runtime_after_restore()

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
                rollback_note = ""
                if restore_started:
                    rollback_note = await self._rollback_failed_restore(
                        db_path=db_path,
                        db_existed_before=db_existed_before,
                        safety_backup=safety_backup,
                        config_path=config_path,
                        config_modified=config_modified,
                        config_existed_before=config_existed_before,
                        config_snapshot=config_snapshot,
                        omemo_path=omemo_path,
                        omemo_modified=omemo_modified,
                        omemo_existed_before=omemo_existed_before,
                        omemo_snapshot=omemo_snapshot,
                    )
                return False, str(exc) + rollback_note


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
