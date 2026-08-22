"""Runtime configuration mutation helpers."""

from __future__ import annotations

import ast
import asyncio
import importlib
import logging
import os
import pprint
from typing import Any

import config

from ..config_loader import format_config_import_error
from ..locks import database_file_lock

log = logging.getLogger(__name__)

class ConfigRuntimeMixin:

    def _format_config_changes(self, before: dict[str, object], after: dict[str, object]) -> list[str]:
        changes = []
        for key in self.CONFIG_KEYS:
            old = before.get(key)
            new = after.get(key)
            if old != new:
                changes.append(f"- {key}: {old!r} → {new!r}")
        return changes

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
        text = raw.strip()
        if text.lower() == "true":
            return True
        if text.lower() == "false":
            return False
        if text.lower() == "none":
            return None
        try:
            return ast.literal_eval(text)
        except Exception:
            return raw

    def render_config_assignment(self, key: str, value: Any) -> str:
        return f"{key} = {pprint.pformat(value, width=88, sort_dicts=False)}"

    @staticmethod
    def _replace_config_assignment_text(
        text: str,
        key: str,
        assignment: str,
        *,
        filename: str = "config.py",
    ) -> str:
        """Replace one top-level assignment without leaving multiline remnants."""
        tree = ast.parse(text, filename=filename)
        matching_nodes: list[ast.AST] = []
        for node in tree.body:
            if isinstance(node, ast.Assign):
                names = [
                    target.id
                    for target in node.targets
                    if isinstance(target, ast.Name)
                ]
                if key in names:
                    matching_nodes.append(node)
            elif (
                isinstance(node, ast.AnnAssign)
                and isinstance(node.target, ast.Name)
                and node.target.id == key
            ):
                matching_nodes.append(node)

        if len(matching_nodes) > 1:
            raise ValueError(f"Config contains multiple top-level assignments for {key}")

        lines = text.splitlines(keepends=True)
        if matching_nodes:
            node = matching_nodes[0]
            start = int(node.lineno) - 1
            end = int(getattr(node, "end_lineno", node.lineno))
            newline = "\r\n" if lines[start].endswith("\r\n") else "\n"
            lines[start:end] = [assignment + newline]
            updated = "".join(lines)
        else:
            updated = text.rstrip() + "\n\n# Runtime config edits\n" + assignment + "\n"

        # Never install a config.py which Python itself cannot import.
        compile(updated, filename, "exec")
        return updated

    @staticmethod
    def _write_config_text_atomic(path, text: str) -> None:
        tmp_path = path.with_name(f".{path.name}.tmp-{os.getpid()}")
        try:
            with open(tmp_path, "w", encoding="utf8") as handle:
                # config.py contains credentials. Do not rely on the process umask:
                # legacy/manual deployments commonly run with umask 022.
                os.chmod(tmp_path, 0o600)
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_path, path)
            os.chmod(path, 0o600)
        finally:
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError as exc:
                log.debug("Failed to remove temporary config file %s: %s", tmp_path, exc)

        try:
            dir_fd = os.open(path.parent, os.O_DIRECTORY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except OSError as exc:
            log.debug("Failed to fsync config directory %s: %s", path.parent, exc)

    def update_config_file_assignment(self, key: str, value: Any) -> None:
        path = self._config_file_path()
        text = path.read_text(encoding="utf8")
        assignment = self.render_config_assignment(key, value)
        updated = self._replace_config_assignment_text(
            text,
            key,
            assignment,
            filename=str(path),
        )
        self._write_config_text_atomic(path, updated)

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
        previous_module_values = {name: getattr(config, name, None) for name in (*self.CONFIG_KEYS, *self.STARTUP_ONLY_CONFIG_KEYS)}
        setattr(config, key, new_value)
        errors, warnings = self._validate_config()
        if errors:
            self._restore_config_values(previous_module_values)
            return False, "Invalid value; config.py was not changed.\n" + self._format_config_validation(errors, warnings)

        create_backup = getattr(self, "create_database_backup", None)
        if callable(create_backup):
            backup_ok, backup_message = await create_backup("before-config", actor=actor or "unknown", lock=False)
            if not backup_ok:
                self._restore_config_values(previous_module_values)
                return False, f"Config was not changed because pre-change backup failed: {backup_message}"

        config_path = self._config_file_path()
        try:
            original_config_text = config_path.read_text(encoding="utf8")
        except OSError as exc:
            self._restore_config_values(previous_module_values)
            return False, f"Failed to read config.py before update: {exc}"

        try:
            self.update_config_file_assignment(key, new_value)
            importlib.reload(config)
            self.apply_runtime_config()
            await self.update_vcard()
        except Exception as exc:
            rollback_error: Exception | None = None
            try:
                self._write_config_text_atomic(config_path, original_config_text)
                importlib.reload(config)
                self.apply_runtime_config()
            except Exception as rollback_exc:
                rollback_error = rollback_exc
                self._restore_config_values(previous_module_values)

            message = format_config_import_error(exc)
            if rollback_error is not None:
                message += (
                    "\n⚠️ Failed to restore the previous config.py cleanly: "
                    + format_config_import_error(rollback_error)
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
        """Reload config.py, validate it, apply reloadable settings, and return (changes, errors, warnings).

        Startup-only settings (bot identity, admin room, DB file, RTBL enable/publish
        setup, and OMEMO setup) are intentionally not applied at runtime. If they
        changed, the old in-memory
        values remain active and a restart warning is returned.
        """
        before = self._runtime_config_snapshot()
        startup_before = self._startup_config_snapshot()
        restore_keys = self.CONFIG_KEYS + self.STARTUP_ONLY_CONFIG_KEYS
        previous_module_values = {key: getattr(config, key, None) for key in restore_keys}

        try:
            importlib.reload(config)
        except Exception as e:
            # Keep the last known good config module values for code paths that read config directly.
            self._restore_config_values(previous_module_values)
            return [], [format_config_import_error(e)], []

        errors, warnings = self._validate_config()
        if errors:
            # Keep the last known good config module values for code paths that read config directly.
            self._restore_config_values(previous_module_values)
            return [], errors, warnings

        startup_after = self._startup_config_snapshot()
        startup_changes = self._format_startup_only_changes(startup_before, startup_after)
        if startup_changes:
            warnings.append(
                "Startup-only config changes detected and NOT applied. Restart the bot to activate:\n"
                + "\n".join(startup_changes)
            )
            # Restore startup-only values so the running process stays internally consistent.
            for key in self.STARTUP_ONLY_CONFIG_KEYS:
                if key in previous_module_values:
                    setattr(config, key, previous_module_values[key])

        self.apply_runtime_config()
        await self.update_vcard()

        after = self._runtime_config_snapshot()
        changes = self._format_config_changes(before, after)
        return changes, [], warnings
