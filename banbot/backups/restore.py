"""Managed backup restore helpers."""

from __future__ import annotations

import asyncio
import logging
import pathlib
import tempfile

from envs_xmpp_core.storage.restore import (
    RestoreFileSpec,
    RestoreTransactionError,
    run_restore_transaction,
)

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

    async def _prepare_runtime_for_restore(self) -> None:
        """Commit and close live DB connections before exact rollback snapshots."""
        close_outbox = getattr(self, "close_outbox_storage", None)
        if callable(close_outbox):
            await close_outbox()
        db = getattr(self, "db", None)
        if db is None:
            return
        await db.commit()
        await db.close()
        self.db = None

    async def _close_runtime_before_restore_rollback(self) -> None:
        """Close any DB connections opened while validating restored state."""
        close_outbox = getattr(self, "close_outbox_storage", None)
        if callable(close_outbox):
            await close_outbox()
        db = getattr(self, "db", None)
        if db is None:
            return
        try:
            await db.close()
        finally:
            self.db = None

    async def _recover_unmodified_restore_runtime(self) -> None:
        """Re-open runtime state when preparation succeeded but no file changed."""
        if getattr(self, "db", None) is None:
            await self._reload_database_runtime_after_restore()

    async def _validate_restored_runtime(self, db_path: pathlib.Path) -> None:
        """Validate the published DB and reload all DB-backed runtime state."""
        restored_ok, restored_message = await self._check_sqlite_integrity(db_path)
        if not restored_ok:
            raise RuntimeError(
                "Restore aborted: restored DB failed integrity_check: "
                f"{restored_message}"
            )
        await self._reload_database_runtime_after_restore()

    @staticmethod
    def _restore_transaction_failure_message(exc: RestoreTransactionError) -> str:
        """Preserve BanBot's user-facing rollback/recovery diagnostics."""
        errors = (*exc.rollback_errors, *exc.recovery_errors)
        if exc.rollback_attempted:
            if errors:
                return str(exc.cause) + " Rollback incomplete: " + "; ".join(
                    str(item) for item in errors
                )
            return (
                str(exc.cause)
                + " Previous files were restored. Database connection/runtime state was recovered."
            )
        if exc.recovery_attempted:
            if errors:
                return str(exc.cause) + " Runtime recovery failed: " + "; ".join(
                    str(item) for item in errors
                )
            return str(exc.cause) + " Database connection/runtime state was recovered."
        return str(exc.cause)

    async def restore_database_backup(self, name: str, *, actor: str | None = None) -> tuple[bool, str]:
        """Restore a managed database backup and reload DB-backed caches."""
        async with database_mutation_locks(self):
            return await self._restore_database_backup_locked(name, actor=actor)

    async def _restore_database_backup_locked(self, name: str, *, actor: str | None = None) -> tuple[bool, str]:
        """Restore implementation using the shared exact-file transaction core."""
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
            if database_source is None:
                return False, "Restore aborted: selected backup contains no database."
            verify_ok, verify_message = await self._check_sqlite_integrity(database_source)
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

            config_source = sources.get("config")
            config_path = self._config_path()
            config_will_restore = config_source is not None and config_path is not None

            omemo_source = sources.get("omemo")
            omemo_path = self._omemo_storage_path()
            omemo_will_restore = omemo_source is not None and omemo_path is not None

            restore_specs = [
                RestoreFileSpec(
                    name="database",
                    source=database_source,
                    target=db_path,
                    mode=0o600,
                )
            ]
            if config_will_restore and config_source is not None and config_path is not None:
                restore_specs.append(
                    RestoreFileSpec(
                        name="config",
                        source=config_source,
                        target=config_path,
                        mode=0o600,
                    )
                )
            if omemo_will_restore and omemo_source is not None and omemo_path is not None:
                restore_specs.append(
                    RestoreFileSpec(
                        name="omemo",
                        source=omemo_source,
                        target=omemo_path,
                        mode=0o600,
                        parent_mode=0o700,
                    )
                )

            try:
                await run_restore_transaction(
                    restore_specs,
                    rollback_directory=tmp_dir / "rollback",
                    prepare=self._prepare_runtime_for_restore,
                    validate=lambda: self._validate_restored_runtime(db_path),
                    before_rollback=self._close_runtime_before_restore_rollback,
                    after_rollback=self._reload_database_runtime_after_restore,
                    recover_unmodified=self._recover_unmodified_restore_runtime,
                )
            except RestoreTransactionError as exc:
                log.error("Failed to restore database backup %s: %s", backup.path, exc)
                return False, self._restore_transaction_failure_message(exc)

            restored_config = config_will_restore
            restored_omemo = omemo_will_restore
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
                lines.append(
                    f"⚠️ Run {command_prefix}reloadconfig or restart the bot to apply restored config.py values."
                )
            elif config_source is not None:
                lines.append(
                    "config.py backup companion exists, but no active config.py path was available for restore."
                )
            else:
                lines.append("No config.py companion file was found for this backup.")
            if restored_omemo:
                lines.append("OMEMO storage was restored from the backup archive.")
                lines.append("⚠️ Restart the bot before using restored OMEMO sessions.")
            elif omemo_source is not None:
                lines.append(
                    "OMEMO backup companion exists, but no OMEMO_STORAGE_FILE path was available for restore."
                )
            else:
                lines.append("No OMEMO companion file was found for this backup.")
            if safety_ok:
                lines.append(f"Safety backup before restore: {safety_message}")
            return True, "\n".join(lines)


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
