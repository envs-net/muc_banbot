"""Runtime configuration mutation helpers."""

from __future__ import annotations

import asyncio
import logging
import pprint
from typing import TYPE_CHECKING, Any

from envs_xmpp_core.config.changes import config_value_changes
from envs_xmpp_core.config.literals import parse_literal
from envs_xmpp_core.config.python_file import (
    ConfigFileTransactionError,
    apply_config_edit_transaction,
    prepare_assignment_edit,
)

import config

from ..config_loader import format_config_import_error, reload_config_module
from ..locks import database_file_lock
from .imports import restore_config_module_state, snapshot_config_module_state

log = logging.getLogger(__name__)

if TYPE_CHECKING:
    from ..contracts import ConfigRuntimeMixinHost

    class _ConfigRuntimeMixinContract(ConfigRuntimeMixinHost):
        pass
else:
    class _ConfigRuntimeMixinContract:
        pass


class ConfigRuntimeMixin(_ConfigRuntimeMixinContract):
    muc_write_limit: int
    muc_write_semaphore: asyncio.Semaphore

    def _format_config_changes(self, before: dict[str, object], after: dict[str, object]) -> list[str]:
        return [
            f"- {change.key}: {change.before!r} → {change.after!r}"
            for change in config_value_changes(before, after, keys=self.CONFIG_KEYS)
        ]

    def apply_log_level(self, level_name: str | None = None) -> str:
        """
        Apply Python logging level at runtime.

        Returns the effective level name.
        """
        if level_name is None:
            level_name = getattr(config, "LOG_LEVEL", "INFO")

        level_name = str(level_name).upper().strip()
        level = getattr(logging, level_name, None)

        if not isinstance(level, int):
            log.warning("Invalid LOG_LEVEL=%r, falling back to INFO", level_name)
            level_name = "INFO"
            level = logging.INFO

        old_level = getattr(self, "log_level", None)

        logging.getLogger().setLevel(level)
        logging.getLogger("banbot").setLevel(level)

        # Keep noisy third-party libraries readable.
        # In DEBUG mode, keep them at INFO instead of DEBUG.
        # In WARNING/ERROR mode, follow the configured stricter level.
        third_party_level = max(level, logging.INFO)
        logging.getLogger("slixmpp").setLevel(third_party_level)
        logging.getLogger("aiosqlite").setLevel(third_party_level)

        self.log_level = level_name

        if old_level != level_name:
            log.info("Log level set to %s", level_name)

        return level_name

    def apply_runtime_config(self) -> None:
        """Load reloadable runtime settings from config."""
        errors, warnings = self._validate_config()
        for warning in warnings:
            log.warning("Config warning: %s", warning)
        if errors:
            raise ValueError("Invalid config:\n" + "\n".join(f"- {e}" for e in errors))

        new_semaphore_value = getattr(config, "MUC_WRITE_SEMAPHORE", 5)
        if new_semaphore_value != self.muc_write_limit:
            old_value = self.muc_write_limit
            self.muc_write_limit = new_semaphore_value
            self.muc_write_semaphore = asyncio.Semaphore(new_semaphore_value)
            log.info("🔄 MUC_WRITE_SEMAPHORE updated: %d → %d", old_value, new_semaphore_value)

        self.apply_log_level(getattr(config, "LOG_LEVEL", "INFO"))

        self.command_prefix = str(getattr(config, "COMMAND_PREFIX", "!")).strip() or "!"
        self.sync_batch_size = getattr(config, "SYNC_BATCH_SIZE", 10)
        self.list_page_size = getattr(config, "LIST_PAGE_SIZE", 10)
        self.config_output_mode = str(getattr(config, "CONFIG_OUTPUT_MODE", "all")).lower().strip()
        self.help_output_mode = str(getattr(config, "HELP_OUTPUT_MODE", "all")).lower().strip()
        self.db_backup_on_start = getattr(config, "DB_BACKUP_ON_START", True)
        self.db_backup_dir = str(getattr(config, "DB_BACKUP_DIR", "data/backups")).strip() or "data/backups"
        self.db_backup_keep = getattr(config, "DB_BACKUP_KEEP", 15)
        self.db_backup_include_omemo = getattr(config, "DB_BACKUP_INCLUDE_OMEMO", True)
        self.export_dir = str(getattr(config, "EXPORT_DIR", "data/exports")).strip() or "data/exports"
        self.export_keep = getattr(config, "EXPORT_KEEP", 15)
        self.announce_startup = getattr(config, "ANNOUNCE_STARTUP", True)
        self.announce_sync_details = getattr(config, "ANNOUNCE_SYNC_DETAILS", True)
        self.structured_event_logs = getattr(config, "STRUCTURED_EVENT_LOGS", True)
        self.audit_log_enabled = getattr(config, "AUDIT_LOG_ENABLED", True)
        self.audit_log_retention_days = getattr(config, "AUDIT_LOG_RETENTION_DAYS", 365)
        self.show_ban_in_muc = getattr(config, "SHOW_BAN_IN_MUC", False)
        self.allow_user_cmds = getattr(config, "ALLOW_USER_COMMANDS_IN_PROTECTED_ROOMS", True)
        self.allow_admin_commands_in_dms = getattr(config, "ALLOW_ADMIN_COMMANDS_IN_DMS", True)
        self.room_invites_enabled = getattr(config, "ROOM_INVITES_ENABLED", False)
        self.room_invite_max_age_days = getattr(config, "ROOM_INVITE_MAX_AGE_DAYS", 30)

        self.alert_on_reconnect = getattr(config, "ALERT_ON_RECONNECT", True)
        self.alert_on_admin_rights_lost = getattr(config, "ALERT_ON_ADMIN_RIGHTS_LOST", True)
        self.alert_on_health_check_failure = getattr(config, "ALERT_ON_HEALTH_CHECK_FAILURE", True)
        self.alert_on_db_stats_failure = getattr(config, "ALERT_ON_DB_STATS_FAILURE", True)
        self.alert_on_redaction_failure = getattr(config, "ALERT_ON_REDACTION_FAILURE", True)
        self.alert_on_db_size_mb = getattr(config, "ALERT_ON_DB_SIZE_MB", 0)
        self.alert_on_rtbl_refresh_failures = getattr(config, "ALERT_ON_RTBL_REFRESH_FAILURES", 3)
        self.alert_dedup_window = getattr(config, "ALERT_DEDUP_WINDOW", 300)
        self.outbox_enabled = getattr(config, "OUTBOX_ENABLED", True)
        self.outbox_retry_initial_seconds = getattr(config, "OUTBOX_RETRY_INITIAL_SECONDS", 30)
        self.outbox_retry_max_seconds = getattr(config, "OUTBOX_RETRY_MAX_SECONDS", 1800)
        self.outbox_max_attempts = getattr(config, "OUTBOX_MAX_ATTEMPTS", 12)
        self.outbox_batch_size = getattr(config, "OUTBOX_BATCH_SIZE", 20)
        self.outbox_poll_seconds = getattr(config, "OUTBOX_POLL_SECONDS", 5)
        self.outbox_max_pending = getattr(config, "OUTBOX_MAX_PENDING", 10000)
        self.outbox_max_bytes = getattr(config, "OUTBOX_MAX_BYTES", 50 * 1024 * 1024)
        self.outbox_max_per_destination = getattr(config, "OUTBOX_MAX_PER_DESTINATION", 1000)
        self.outbox_max_per_category = getattr(config, "OUTBOX_MAX_PER_CATEGORY", 5000)

        self.health_check_interval = getattr(config, "HEALTH_CHECK_INTERVAL", 300)
        self.muc_join_timeout_seconds = getattr(config, "MUC_JOIN_TIMEOUT_SECONDS", 20)
        self.muc_join_retries = getattr(config, "MUC_JOIN_RETRIES", 2)
        self.unban_check_interval = getattr(config, "UNBAN_CHECK_INTERVAL", 60)
        self.max_tempban_days = getattr(config, "MAX_TEMPBAN_DAYS", 30)
        self.public_command_rate_limit_window = getattr(config, "PUBLIC_COMMAND_RATE_LIMIT_WINDOW", 10)
        self.public_command_rate_limit_max = getattr(config, "PUBLIC_COMMAND_RATE_LIMIT_MAX", 3)

        self.version_check_enabled = getattr(config, "VERSION_CHECK_ENABLED", False)
        self.version_check_interval = getattr(config, "VERSION_CHECK_INTERVAL", 3600)
        self.version_check_url = str(getattr(config, "VERSION_CHECK_URL", "")).strip() or None

        self.rtbl_announce = getattr(config, "RTBL_ANNOUNCE", True)
        self.rtbl_refresh_interval = getattr(config, "RTBL_REFRESH_INTERVAL", 3600)

        self.redaction_enabled = getattr(config, "REDACTION_ENABLED", False)
        self.redaction_index_retention_days = getattr(config, "REDACTION_INDEX_RETENTION_DAYS", 30)
        self.auto_redact_on_imported_ban_reason = getattr(config, "AUTO_REDACT_ON_IMPORTED_BAN_REASON", False)
        self.auto_redact_on_manual_muc_ban = getattr(config, "AUTO_REDACT_ON_MANUAL_MUC_BAN", True)
        self.redaction_auto_reasons = list(getattr(config, "REDACTION_AUTO_REASONS", []))
        self.redaction_retract_concurrency = getattr(config, "REDACTION_RETRACT_CONCURRENCY", 10)
        self.redaction_iq_timeout_seconds = getattr(config, "REDACTION_IQ_TIMEOUT_SECONDS", 5)

    def parse_config_value(self, raw: str) -> Any:
        return parse_literal(raw)

    def render_config_assignment(self, key: str, value: Any) -> str:
        return f"{key} = {pprint.pformat(value, width=88, sort_dicts=False)}"

    def update_config_file_assignment(self, key: str, value: Any) -> None:
        edit = prepare_assignment_edit(
            self._config_file_path(),
            key,
            self.render_config_assignment(key, value),
            mode=0o600,
        )
        edit.write()

    async def set_runtime_config_value(
        self,
        key: str,
        raw_value: str,
        *,
        actor: str | None = None,
        _locked: bool = False,
    ) -> tuple[bool, str]:
        if not _locked:
            async with database_file_lock(self):
                return await self.set_runtime_config_value(
                    key,
                    raw_value,
                    actor=actor,
                    _locked=True,
                )

        key = key.upper().strip()
        if key not in self.CONFIG_KEYS:
            return False, f"{key} is not a runtime-writable config option."
        if key in self.CONFIG_NEVER_WRITABLE_KEYS:
            return False, f"{key} cannot be changed via chat command."

        old_value = getattr(config, key, None)
        new_value = self.parse_config_value(raw_value)
        previous_module_state = snapshot_config_module_state()

        setattr(config, key, new_value)
        try:
            errors, warnings = self._validate_config()
        except Exception as exc:
            return False, f"Failed to validate config value: {exc}"
        finally:
            restore_config_module_state(previous_module_state)
        if errors:
            return False, "Invalid value; config.py was not changed.\n" + self._format_config_validation(errors, warnings)

        create_backup = getattr(self, "create_database_backup", None)
        if callable(create_backup):
            backup_ok, backup_message = await create_backup("before-config", actor=actor or "unknown", lock=False)
            if not backup_ok:
                return False, f"Config was not changed because pre-change backup failed: {backup_message}"

        config_path = self._config_file_path()
        try:
            edit = prepare_assignment_edit(
                config_path,
                key,
                self.render_config_assignment(key, new_value),
                mode=0o600,
            )
        except OSError as exc:
            return False, f"Failed to read config.py before update: {exc}"
        except Exception as exc:
            return False, f"Failed to write/apply config: {format_config_import_error(exc)}"

        async def apply_candidate() -> None:
            reload_config_module(config)
            self.apply_runtime_config()
            await self.update_vcard()

        async def rollback_runtime(file_restored: bool) -> None:
            if file_restored:
                try:
                    reload_config_module(config)
                except Exception:
                    # Keep runtime state recoverable even if reloading the
                    # restored file itself unexpectedly fails.
                    restore_config_module_state(previous_module_state)
                    self.apply_runtime_config()
                    raise
            else:
                restore_config_module_state(previous_module_state)

            self.apply_runtime_config()
            await self.update_vcard()

        try:
            await apply_config_edit_transaction(
                edit,
                apply=apply_candidate,
                rollback_apply=rollback_runtime,
            )
        except ConfigFileTransactionError as exc:
            message = format_config_import_error(exc.error)
            if exc.rollback_errors:
                rollback_message = "; ".join(
                    format_config_import_error(error)
                    for error in exc.rollback_errors
                    if isinstance(error, Exception)
                )
                if not rollback_message:
                    rollback_message = "; ".join(str(error) for error in exc.rollback_errors)
                message += (
                    "\n⚠️ Failed to restore the previous config.py cleanly: "
                    + rollback_message
                )
            return False, f"Failed to write/apply config: {message}"

        return True, f"✅ {key} updated: {old_value!r} → {new_value!r}"

    async def unset_runtime_config_value(
        self,
        key: str,
        *,
        actor: str | None = None,
        _locked: bool = False,
    ) -> tuple[bool, str]:
        if not _locked:
            async with database_file_lock(self):
                return await self.unset_runtime_config_value(
                    key,
                    actor=actor,
                    _locked=True,
                )

        key = key.upper().strip()
        defaults = self._config_default_values_from_sample()
        if key not in defaults:
            return False, f"No default value found for {key} in config_sample.py."
        return await self.set_runtime_config_value(
            key,
            repr(defaults[key]),
            actor=actor,
            _locked=True,
        )

    async def reload_runtime_config(self) -> tuple[list[str], list[str], list[str]]:
        """Reload and atomically apply runtime configuration.

        Config reload shares the canonical database/file lock with chat edits,
        backups, restores and managed exports so an operator reload cannot read
        a half-transitioned config state. Startup-only settings remain at their
        last-known-good in-process values until restart. If live application is
        cancelled or fails after validation, both the config module namespace
        and already-mutated runtime attributes are restored before returning.
        """
        async with database_file_lock(self):
            before = self._runtime_config_snapshot()
            startup_before = self._startup_config_snapshot()
            previous_module_state = snapshot_config_module_state()
            startup_restore_keys = (*self.STARTUP_ONLY_CONFIG_KEYS, "RESSOURCE")
            previous_startup_values = self._snapshot_config_values(startup_restore_keys)

            try:
                reload_config_module(config)
            except Exception as exc:
                # The loader itself is transactional, but restore the exact
                # namespace as an additional invariant for injected/custom
                # loaders and lightweight test doubles.
                restore_config_module_state(previous_module_state)
                return [], [format_config_import_error(exc)], []

            try:
                errors, warnings = self._validate_config()
            except Exception as exc:
                restore_config_module_state(previous_module_state)
                return [], [f"Config validation failed: {exc}"], []
            if errors:
                restore_config_module_state(previous_module_state)
                return [], errors, warnings

            startup_after = self._startup_config_snapshot()
            startup_changes = self._format_startup_only_changes(startup_before, startup_after)
            if startup_changes:
                warnings.append(
                    "Startup-only config changes detected and NOT applied. Restart the bot to activate:\n"
                    + "\n".join(startup_changes)
                )
                # RESOURCE historically existed as the misspelled RESSOURCE.
                # Restore both names so legacy configurations remain internally
                # consistent when a reload changes only startup identity.
                self._restore_config_values(previous_startup_values)

            async def rollback_live_state() -> list[str]:
                rollback_errors: list[str] = []
                restore_config_module_state(previous_module_state)
                try:
                    self.apply_runtime_config()
                except Exception as rollback_exc:
                    rollback_errors.append(
                        "runtime rollback failed: " + format_config_import_error(rollback_exc)
                    )
                try:
                    await self.update_vcard()
                except Exception as rollback_exc:
                    rollback_errors.append(f"vCard rollback failed: {rollback_exc}")
                return rollback_errors

            try:
                self.apply_runtime_config()
                await self.update_vcard()
            except asyncio.CancelledError as exc:
                rollback_errors = await rollback_live_state()
                for rollback_error in rollback_errors:
                    exc.add_note(rollback_error)
                raise
            except Exception as exc:
                rollback_errors = await rollback_live_state()
                message = f"Failed to apply reloaded config: {exc}"
                if rollback_errors:
                    message += "; " + "; ".join(rollback_errors)
                return [], [message], warnings

            after = self._runtime_config_snapshot()
            changes = self._format_config_changes(before, after)
            return changes, [], warnings
