"""Configuration loading, validation, reload handling, and startup checks."""

import asyncio
import importlib
import importlib.util
import linecache
import logging
import os
import pathlib
import builtins
import ast
import pprint
import re
from typing import Any

try:
    import config
except ModuleNotFoundError as exc:
    if exc.name != "config":
        raise
    config = None
except Exception:
    # Do not fail while importing config_utils just because config.py is broken.
    # bot.py imports this module to format config import errors for users.
    config = None

from .locks import database_file_lock
from .utils import validate_jid_format

# Allow config.py to use lowercase boolean aliases like in YAML/JSON/TOML.
builtins.true = True
builtins.false = False

log = logging.getLogger(__name__)


def format_config_import_error(exc: BaseException) -> str:
    """Return a helpful config.py import/reload error with file line and source text."""
    filename = "config.py"
    lineno = None
    text = None

    if isinstance(exc, SyntaxError):
        filename = exc.filename or filename
        lineno = exc.lineno
        text = (exc.text or "").strip() or None
    else:
        tb = exc.__traceback__
        while tb:
            frame_filename = tb.tb_frame.f_code.co_filename
            if frame_filename.endswith("config.py") or os.path.basename(frame_filename) == "config.py":
                filename = frame_filename
                lineno = tb.tb_lineno
                text = linecache.getline(frame_filename, lineno).strip() or None
            tb = tb.tb_next

    location = f"{os.path.basename(filename)}"
    if lineno:
        location += f":{lineno}"

    lines = [f"{location}: {exc.__class__.__name__}: {exc}"]
    if text:
        lines.append(f"    {text}")
        if isinstance(exc, SyntaxError) and exc.offset:
            lines.append("    " + " " * max(exc.offset - 1, 0) + "^")

    if isinstance(exc, NameError):
        lines.append("Hint: string values in config.py need quotes.")
        lines.append('Example: CONNECT_HOST = "myhost.com"')
        lines.append("For booleans, use True/False.")
        lines.append("This bot also accepts lowercase true/false.")

    if isinstance(exc, ModuleNotFoundError) and getattr(exc, "name", None) == "config":
        lines.append("Hint: config.py is missing.")
        lines.append("Create it from the sample config first:")
        lines.append("  cp config_sample.py config.py")
        lines.append("Then edit config.py and start the bot again.")

    return "\n".join(lines)


def get_config_resource() -> str | None:
    """Return RESOURCE with backwards-compatible support for legacy RESSOURCE."""
    resource = getattr(config, "RESOURCE", None)
    if resource is not None:
        return resource
    return getattr(config, "RESSOURCE", None)


class ConfigMixin:
    CONFIG_KEYS = (
        "LOG_LEVEL",
        "COMMAND_PREFIX",
        "LIST_PAGE_SIZE",
        "DB_BACKUP_ON_START",
        "DB_BACKUP_DIR",
        "DB_BACKUP_KEEP",
        "DB_BACKUP_INCLUDE_OMEMO",
        "EXPORT_DIR",
        "EXPORT_KEEP",
        "ANNOUNCE_STARTUP",
        "ANNOUNCE_SYNC_DETAILS",
        "SHOW_BAN_IN_MUC",
        "ALLOW_USER_COMMANDS_IN_PROTECTED_ROOMS",
        "ALLOW_ADMIN_COMMANDS_IN_DMS",
        "ROOM_INVITES_ENABLED",
        "ALERT_ON_RECONNECT",
        "ALERT_ON_ADMIN_RIGHTS_LOST",
        "ALERT_ON_HEALTH_CHECK_FAILURE",
        "ALERT_ON_DB_STATS_FAILURE",
        "ALERT_ON_REDACTION_FAILURE",
        "ALERT_ON_DB_SIZE_MB",
        "ALERT_ON_RTBL_REFRESH_FAILURES",
        "ALERT_DEDUP_WINDOW",
        "HEALTH_CHECK_INTERVAL",
        "UNBAN_CHECK_INTERVAL",
        "MAX_TEMPBAN_DAYS",
        "PUBLIC_COMMAND_RATE_LIMIT_WINDOW",
        "PUBLIC_COMMAND_RATE_LIMIT_MAX",
        "MUC_WRITE_SEMAPHORE",
        "SYNC_BATCH_SIZE",
        "STRUCTURED_EVENT_LOGS",
        "AUDIT_LOG_ENABLED",
        "AUDIT_LOG_RETENTION_DAYS",
        "RTBL_ANNOUNCE",
        "RTBL_REFRESH_INTERVAL",
        "REDACTION_ENABLED",
        "REDACTION_INDEX_RETENTION_DAYS",
        "REDACTION_AUTO_REASONS",
        "VERSION_CHECK_ENABLED",
        "VERSION_CHECK_INTERVAL",
        "VERSION_CHECK_URL",
        "AVATAR_PATH",
        "VCARD_NICKNAME",
        "VCARD_FN",
        "VCARD_ORG",
        "VCARD_ROLE",
        "VCARD_URL",
        "VCARD_NOTE",
    )

    STARTUP_ONLY_CONFIG_KEYS = (
        "JID",
        "PASSWORD",
        "RESOURCE",
        "RESSOURCE",  # legacy spelling, kept for backwards compatibility
        "ADMIN_ROOM",
        "NICK",
        "DB_FILE",
        "CONNECT_HOST",
        "CONNECT_PORT",
        "CONNECT_DIRECT_TLS",
        "RTBL_ENABLED",
        "RTBL_PUBLISH_ENABLED",
        "RTBL_PUBLISH_SERVICE",
        "RTBL_PUBLISH_JID_NODE",
        "RTBL_PUBLISH_DOMAIN_NODE",
        "OMEMO_ENABLED",
        "OMEMO_STORAGE_FILE",
        "OMEMO_AUTO_ENCRYPT_ADMIN_ROOM",
        "OMEMO_PLAINTEXT_FALLBACK",
        "OMEMO_RESET_ON_IDENTITY_CHANGE",
    )


    def _runtime_config_snapshot(self) -> dict[str, object]:
        """Return the currently effective runtime config values."""
        return {
            "LOG_LEVEL": getattr(self, "log_level", str(getattr(config, "LOG_LEVEL", "INFO")).upper()),
            "COMMAND_PREFIX": self.command_prefix,
            "LIST_PAGE_SIZE": getattr(self, "list_page_size", 10),
            "DB_BACKUP_ON_START": getattr(self, "db_backup_on_start", True),
            "DB_BACKUP_DIR": getattr(self, "db_backup_dir", "data/backups"),
            "DB_BACKUP_KEEP": getattr(self, "db_backup_keep", 15),
            "DB_BACKUP_INCLUDE_OMEMO": getattr(self, "db_backup_include_omemo", True),
            "EXPORT_DIR": getattr(self, "export_dir", "data/exports"),
            "EXPORT_KEEP": getattr(self, "export_keep", 15),
            "ANNOUNCE_STARTUP": self.announce_startup,
            "ANNOUNCE_SYNC_DETAILS": self.announce_sync_details,
            "SHOW_BAN_IN_MUC": self.show_ban_in_muc,
            "ALLOW_USER_COMMANDS_IN_PROTECTED_ROOMS": self.allow_user_cmds,
            "ALLOW_ADMIN_COMMANDS_IN_DMS": getattr(self, "allow_admin_commands_in_dms", True),
            "ROOM_INVITES_ENABLED": self.room_invites_enabled,
            "ALERT_ON_RECONNECT": getattr(self, "alert_on_reconnect", True),
            "ALERT_ON_ADMIN_RIGHTS_LOST": getattr(self, "alert_on_admin_rights_lost", True),
            "ALERT_ON_HEALTH_CHECK_FAILURE": getattr(self, "alert_on_health_check_failure", True),
            "ALERT_ON_DB_STATS_FAILURE": getattr(self, "alert_on_db_stats_failure", True),
            "ALERT_ON_REDACTION_FAILURE": getattr(self, "alert_on_redaction_failure", True),
            "ALERT_ON_DB_SIZE_MB": getattr(self, "alert_on_db_size_mb", 0),
            "ALERT_ON_RTBL_REFRESH_FAILURES": getattr(self, "alert_on_rtbl_refresh_failures", 3),
            "ALERT_DEDUP_WINDOW": getattr(self, "alert_dedup_window", 300),
            "HEALTH_CHECK_INTERVAL": self.health_check_interval,
            "UNBAN_CHECK_INTERVAL": self.unban_check_interval,
            "MAX_TEMPBAN_DAYS": self.max_tempban_days,
            "PUBLIC_COMMAND_RATE_LIMIT_WINDOW": self.public_command_rate_limit_window,
            "PUBLIC_COMMAND_RATE_LIMIT_MAX": self.public_command_rate_limit_max,
            "MUC_WRITE_SEMAPHORE": self.muc_write_limit,
            "SYNC_BATCH_SIZE": getattr(self, "sync_batch_size", 10),
            "STRUCTURED_EVENT_LOGS": self.structured_event_logs,
            "AUDIT_LOG_ENABLED": self.audit_log_enabled,
            "AUDIT_LOG_RETENTION_DAYS": self.audit_log_retention_days,
            "RTBL_ANNOUNCE": self.rtbl_announce,
            "RTBL_REFRESH_INTERVAL": self.rtbl_refresh_interval,
            "REDACTION_ENABLED": self.redaction_enabled,
            "REDACTION_INDEX_RETENTION_DAYS": self.redaction_index_retention_days,
            "REDACTION_AUTO_REASONS": tuple(self.redaction_auto_reasons),
            "VERSION_CHECK_ENABLED": self.version_check_enabled,
            "VERSION_CHECK_INTERVAL": self.version_check_interval,
            "VERSION_CHECK_URL": self.version_check_url,
            "AVATAR_PATH": getattr(config, "AVATAR_PATH", None),
            "VCARD_NICKNAME": getattr(config, "VCARD_NICKNAME", None),
            "VCARD_FN": getattr(config, "VCARD_FN", None),
            "VCARD_ORG": getattr(config, "VCARD_ORG", None),
            "VCARD_ROLE": getattr(config, "VCARD_ROLE", None),
            "VCARD_URL": getattr(config, "VCARD_URL", None),
            "VCARD_NOTE": getattr(config, "VCARD_NOTE", None),
        }


    def _startup_config_snapshot(self) -> dict[str, object]:
        """Return startup-only config values that cannot be changed via !reloadconfig."""
        return {
            "JID": getattr(config, "JID", None),
            "PASSWORD": getattr(config, "PASSWORD", None),
            "RESOURCE": get_config_resource(),
            "ADMIN_ROOM": getattr(config, "ADMIN_ROOM", None),
            "NICK": getattr(config, "NICK", None),
            "DB_FILE": getattr(config, "DB_FILE", None),
            "RTBL_ENABLED": getattr(config, "RTBL_ENABLED", False),
            "RTBL_PUBLISH_ENABLED": getattr(config, "RTBL_PUBLISH_ENABLED", False),
            "RTBL_PUBLISH_SERVICE": getattr(config, "RTBL_PUBLISH_SERVICE", None),
            "RTBL_PUBLISH_JID_NODE": getattr(config, "RTBL_PUBLISH_JID_NODE", None),
            "RTBL_PUBLISH_DOMAIN_NODE": getattr(config, "RTBL_PUBLISH_DOMAIN_NODE", None),
            "OMEMO_ENABLED": getattr(config, "OMEMO_ENABLED", False),
            "OMEMO_STORAGE_FILE": getattr(config, "OMEMO_STORAGE_FILE", None),
            "OMEMO_AUTO_ENCRYPT_ADMIN_ROOM": getattr(config, "OMEMO_AUTO_ENCRYPT_ADMIN_ROOM", True),
            "OMEMO_PLAINTEXT_FALLBACK": getattr(config, "OMEMO_PLAINTEXT_FALLBACK", False),
            "OMEMO_RESET_ON_IDENTITY_CHANGE": getattr(config, "OMEMO_RESET_ON_IDENTITY_CHANGE", True),
        }


    def _format_startup_only_changes(self, before: dict[str, object], after: dict[str, object]) -> list[str]:
        changes = []
        for key in (
            "JID",
            "PASSWORD",
            "RESOURCE",
            "ADMIN_ROOM",
            "NICK",
            "DB_FILE",
            "RTBL_ENABLED",
            "RTBL_PUBLISH_ENABLED",
            "RTBL_PUBLISH_SERVICE",
            "RTBL_PUBLISH_JID_NODE",
            "RTBL_PUBLISH_DOMAIN_NODE",
            "OMEMO_ENABLED",
            "OMEMO_STORAGE_FILE",
            "OMEMO_AUTO_ENCRYPT_ADMIN_ROOM",
            "OMEMO_PLAINTEXT_FALLBACK",
            "OMEMO_RESET_ON_IDENTITY_CHANGE",
        ):
            old = before.get(key)
            new = after.get(key)
            if old != new:
                changes.append(f"- {key}: {old!r} → {new!r}")
        return changes


    def _restore_config_values(self, values: dict[str, object]) -> None:
        """Restore selected config module values to the last known good values."""
        for key, value in values.items():
            if value is None and key == "RESOURCE" and not hasattr(config, "RESOURCE"):
                continue
            setattr(config, key, value)


    def _validate_config(self) -> tuple[list[str], list[str]]:
        """Validate config.py and return (errors, warnings)."""
        errors: list[str] = []
        warnings: list[str] = []

        def require_non_empty(name: str) -> str:
            value = str(getattr(config, name, "")).strip()
            if not value:
                errors.append(f"{name} must not be empty")
            return value

        jid = require_non_empty("JID")
        password = require_non_empty("PASSWORD")
        admin_room = require_non_empty("ADMIN_ROOM")
        nick = require_non_empty("NICK")
        db_file = require_non_empty("DB_FILE")

        if jid and not validate_jid_format(jid):
            errors.append("JID must be a valid bare JID like bot@example.org")

        if admin_room and not validate_jid_format(admin_room):
            errors.append("ADMIN_ROOM must be a valid room JID like admin@muc.example.org")

        if nick and any(ch.isspace() for ch in nick):
            errors.append("NICK must not contain whitespace")

        placeholder_values = {"yourpassword", "password", "changeme", "change-me"}

        if password.lower() in placeholder_values:
            errors.append("PASSWORD still looks like a placeholder")

        if jid.lower() == "adminbot@domain.tld":
            errors.append("JID still looks like the sample config value")

        if admin_room.lower() == "admin@muc.domain.tld":
            errors.append("ADMIN_ROOM still looks like the sample config value")

        if (
            hasattr(config, "RESOURCE")
            and config.RESOURCE is not None
            and hasattr(config, "RESSOURCE")
            and config.RESSOURCE is not None
            and config.RESOURCE != config.RESSOURCE
        ):
            warnings.append("Both RESOURCE and legacy RESSOURCE are set; RESOURCE will be used")

        log_level = str(getattr(config, "LOG_LEVEL", "INFO")).upper().strip()
        if log_level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            errors.append("LOG_LEVEL must be one of DEBUG, INFO, WARNING, ERROR, CRITICAL")

        command_prefix = str(getattr(config, "COMMAND_PREFIX", "!")).strip()
        if not command_prefix:
            warnings.append("COMMAND_PREFIX is empty; effective value will be '!'")
        elif any(ch.isspace() for ch in command_prefix):
            errors.append("COMMAND_PREFIX must not contain whitespace")

        int_ranges = {
            "AUDIT_LOG_RETENTION_DAYS": (1, 365),
            "HEALTH_CHECK_INTERVAL": (60, 86400),
            "UNBAN_CHECK_INTERVAL": (10, 86400),
            "MAX_TEMPBAN_DAYS": (1, 365),
            "PUBLIC_COMMAND_RATE_LIMIT_WINDOW": (1, 3600),
            "PUBLIC_COMMAND_RATE_LIMIT_MAX": (1, 100),
            "LIST_PAGE_SIZE": (1, 100),
            "MUC_WRITE_SEMAPHORE": (1, 100),
            "SYNC_BATCH_SIZE": (1, 100),
            "VERSION_CHECK_INTERVAL": (300, 86400),
            "ALERT_ON_DB_SIZE_MB": (0, 1048576),
            "ALERT_ON_RTBL_REFRESH_FAILURES": (0, 1000),
            "ALERT_DEDUP_WINDOW": (0, 86400),
            "DB_BACKUP_KEEP": (1, 1000),
            "EXPORT_KEEP": (1, 1000),
        }
        int_defaults = {
            "AUDIT_LOG_RETENTION_DAYS": 365,
            "HEALTH_CHECK_INTERVAL": 300,
            "UNBAN_CHECK_INTERVAL": 60,
            "MAX_TEMPBAN_DAYS": 30,
            "PUBLIC_COMMAND_RATE_LIMIT_WINDOW": 30,
            "PUBLIC_COMMAND_RATE_LIMIT_MAX": 3,
            "LIST_PAGE_SIZE": 10,
            "MUC_WRITE_SEMAPHORE": 5,
            "SYNC_BATCH_SIZE": 10,
            "VERSION_CHECK_INTERVAL": 3600,
            "ALERT_ON_DB_SIZE_MB": 0,
            "ALERT_ON_RTBL_REFRESH_FAILURES": 3,
            "ALERT_DEDUP_WINDOW": 300,
            "DB_BACKUP_KEEP": 15,
            "EXPORT_KEEP": 15,
        }
        for name, (minimum, maximum) in int_ranges.items():
            value = getattr(config, name, int_defaults.get(name))
            if not isinstance(value, int):
                errors.append(f"{name} must be an integer")
                continue
            if value < minimum or value > maximum:
                errors.append(f"{name} must be between {minimum} and {maximum} (got {value})")

        bool_names = (
            "ANNOUNCE_STARTUP",
            "ANNOUNCE_SYNC_DETAILS",
            "STRUCTURED_EVENT_LOGS",
            "AUDIT_LOG_ENABLED",
            "SHOW_BAN_IN_MUC",
            "ALLOW_USER_COMMANDS_IN_PROTECTED_ROOMS",
            "ALLOW_ADMIN_COMMANDS_IN_DMS",
            "ROOM_INVITES_ENABLED",
            "VERSION_CHECK_ENABLED",
            "REDACTION_ENABLED",
            "CONNECT_DIRECT_TLS",
            "DB_BACKUP_ON_START",
            "DB_BACKUP_INCLUDE_OMEMO",
            "ALERT_ON_RECONNECT",
            "ALERT_ON_ADMIN_RIGHTS_LOST",
            "ALERT_ON_HEALTH_CHECK_FAILURE",
            "ALERT_ON_DB_STATS_FAILURE",
            "ALERT_ON_REDACTION_FAILURE",
        )
        bool_defaults = {
            "ANNOUNCE_STARTUP": True,
            "ANNOUNCE_SYNC_DETAILS": True,
            "STRUCTURED_EVENT_LOGS": True,
            "AUDIT_LOG_ENABLED": True,
            "SHOW_BAN_IN_MUC": False,
            "ALLOW_USER_COMMANDS_IN_PROTECTED_ROOMS": True,
            "ALLOW_ADMIN_COMMANDS_IN_DMS": True,
            "ROOM_INVITES_ENABLED": False,
            "VERSION_CHECK_ENABLED": False,
            "REDACTION_ENABLED": False,
            "CONNECT_DIRECT_TLS": False,
            "DB_BACKUP_ON_START": True,
            "DB_BACKUP_INCLUDE_OMEMO": True,
            "ALERT_ON_RECONNECT": True,
            "ALERT_ON_ADMIN_RIGHTS_LOST": True,
            "ALERT_ON_HEALTH_CHECK_FAILURE": True,
            "ALERT_ON_DB_STATS_FAILURE": True,
            "ALERT_ON_REDACTION_FAILURE": True,
        }
        for name in bool_names:
            if not isinstance(getattr(config, name, bool_defaults.get(name)), bool):
                errors.append(f"{name} must be True or False")

        version_url = str(getattr(config, "VERSION_CHECK_URL", "")).strip()
        if getattr(config, "VERSION_CHECK_ENABLED", False) and not version_url:
            errors.append("VERSION_CHECK_URL must not be empty when VERSION_CHECK_ENABLED=True")
        if version_url and not version_url.startswith(("http://", "https://")):
            errors.append("VERSION_CHECK_URL must start with http:// or https://")

        avatar_path = getattr(config, "AVATAR_PATH", None)
        if avatar_path and not pathlib.Path(str(avatar_path)).exists():
            warnings.append(f"AVATAR_PATH does not exist: {avatar_path}")

        if db_file:
            db_parent = pathlib.Path(db_file).expanduser().parent
            if str(db_parent) not in ("", ".") and not db_parent.exists():
                errors.append(f"DB_FILE directory does not exist: {db_parent}")

        backup_dir_value = getattr(config, "DB_BACKUP_DIR", "data/backups")
        if not isinstance(backup_dir_value, str):
            errors.append("DB_BACKUP_DIR must be a string")
            backup_dir = ""
        else:
            backup_dir = backup_dir_value.strip()
        if not backup_dir:
            errors.append("DB_BACKUP_DIR must not be empty")

        export_dir_value = getattr(config, "EXPORT_DIR", "data/exports")
        if not isinstance(export_dir_value, str):
            errors.append("EXPORT_DIR must be a string")
            export_dir = ""
        else:
            export_dir = export_dir_value.strip()
        if not export_dir:
            errors.append("EXPORT_DIR must not be empty")

        # --- Connection ---
        connect_host = getattr(config, "CONNECT_HOST", None)
        if connect_host is not None and not isinstance(connect_host, str):
            errors.append("CONNECT_HOST must be a string or None")

        connect_port = getattr(config, "CONNECT_PORT", 5222)
        if not isinstance(connect_port, int) or not (1 <= connect_port <= 65535):
            errors.append("CONNECT_PORT must be an integer between 1 and 65535")

        # --- RTBL ---
        if not isinstance(getattr(config, "RTBL_ENABLED", False), bool):
            errors.append("RTBL_ENABLED must be True or False")
        if not isinstance(getattr(config, "RTBL_ANNOUNCE", True), bool):
            errors.append("RTBL_ANNOUNCE must be True or False")

        rtbl_refresh = getattr(config, "RTBL_REFRESH_INTERVAL", 3600)
        if not isinstance(rtbl_refresh, int) or rtbl_refresh < 0:
            errors.append("RTBL_REFRESH_INTERVAL must be a non-negative integer (0 = disabled)")

        # --- Redaction ---
        redaction_retention = getattr(config, "REDACTION_INDEX_RETENTION_DAYS", 30)
        if not isinstance(redaction_retention, int) or redaction_retention < 0:
            errors.append("REDACTION_INDEX_RETENTION_DAYS must be a non-negative integer (0 = keep forever)")
        redaction_reasons = getattr(config, "REDACTION_AUTO_REASONS", [])
        if not isinstance(redaction_reasons, (list, tuple)) or not all(isinstance(item, str) for item in redaction_reasons):
            errors.append("REDACTION_AUTO_REASONS must be a list of strings")

        # --- RTBL Publish ---
        rtbl_pub = getattr(config, "RTBL_PUBLISH_ENABLED", False)
        if not isinstance(rtbl_pub, bool):
            errors.append("RTBL_PUBLISH_ENABLED must be True or False")

        if rtbl_pub:
            pub_service = str(getattr(config, "RTBL_PUBLISH_SERVICE", "")).strip()
            pub_jid_node = str(getattr(config, "RTBL_PUBLISH_JID_NODE", "")).strip()
            pub_domain_node = str(getattr(config, "RTBL_PUBLISH_DOMAIN_NODE", "")).strip()
            if not pub_service:
                errors.append("RTBL_PUBLISH_SERVICE must not be empty when RTBL_PUBLISH_ENABLED=True")
            elif "." not in pub_service:
                errors.append("RTBL_PUBLISH_SERVICE must be a valid JID like pubsub.domain.tld")
            if not pub_jid_node:
                errors.append("RTBL_PUBLISH_JID_NODE must not be empty when RTBL_PUBLISH_ENABLED=True")
            if not pub_domain_node:
                errors.append("RTBL_PUBLISH_DOMAIN_NODE must not be empty when RTBL_PUBLISH_ENABLED=True")

        # --- OMEMO ---
        omemo_enabled = getattr(config, "OMEMO_ENABLED", False)
        if not isinstance(omemo_enabled, bool):
            errors.append("OMEMO_ENABLED must be True or False")

        for name, default in (
            ("OMEMO_AUTO_ENCRYPT_ADMIN_ROOM", True),
            ("OMEMO_PLAINTEXT_FALLBACK", False),
            ("OMEMO_RESET_ON_IDENTITY_CHANGE", True),
        ):
            if not isinstance(getattr(config, name, default), bool):
                errors.append(f"{name} must be True or False")

        if omemo_enabled:
            if importlib.util.find_spec("slixmpp_omemo") is None or importlib.util.find_spec("omemo") is None:
                warnings.append(
                    "OMEMO_ENABLED=True but optional OMEMO dependencies are not installed; "
                    "the bot will start with OMEMO disabled. Install requirements-omemo.txt "
                    "after installing system libraries such as libsodium-dev and libxeddsa-dev."
                )

            omemo_storage_raw = str(getattr(config, "OMEMO_STORAGE_FILE", "data/omemo.json")).strip()
            if not omemo_storage_raw:
                errors.append("OMEMO_STORAGE_FILE must not be empty when OMEMO_ENABLED=True")
            else:
                omemo_storage = pathlib.Path(omemo_storage_raw).expanduser()
                if str(omemo_storage.parent) not in ("", ".") and not omemo_storage.parent.exists():
                    warnings.append(f"OMEMO_STORAGE_FILE directory will be created with private permissions if possible: {omemo_storage.parent}")

        return errors, warnings


    def _format_config_validation(self, errors: list[str], warnings: list[str]) -> str:
        lines = []
        if errors:
            lines.append("❌ Config validation errors:")
            lines.extend(f"- {e}" for e in errors)
        if warnings:
            lines.append("⚠️ Config validation warnings:")
            lines.extend(f"- {w}" for w in warnings)
        if not lines:
            lines.append("✅ Config validation passed.")
        return "\n".join(lines)


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
        self.list_page_size = getattr(config, "LIST_PAGE_SIZE", 10)
        self.sync_batch_size = getattr(config, "SYNC_BATCH_SIZE", 10)
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

        self.alert_on_reconnect = getattr(config, "ALERT_ON_RECONNECT", True)
        self.alert_on_admin_rights_lost = getattr(config, "ALERT_ON_ADMIN_RIGHTS_LOST", True)
        self.alert_on_health_check_failure = getattr(config, "ALERT_ON_HEALTH_CHECK_FAILURE", True)
        self.alert_on_db_stats_failure = getattr(config, "ALERT_ON_DB_STATS_FAILURE", True)
        self.alert_on_redaction_failure = getattr(config, "ALERT_ON_REDACTION_FAILURE", True)
        self.alert_on_db_size_mb = getattr(config, "ALERT_ON_DB_SIZE_MB", 0)
        self.alert_on_rtbl_refresh_failures = getattr(config, "ALERT_ON_RTBL_REFRESH_FAILURES", 3)
        self.alert_dedup_window = getattr(config, "ALERT_DEDUP_WINDOW", 300)

        self.health_check_interval = getattr(config, "HEALTH_CHECK_INTERVAL", 300)
        self.unban_check_interval = getattr(config, "UNBAN_CHECK_INTERVAL", 60)
        self.max_tempban_days = getattr(config, "MAX_TEMPBAN_DAYS", 30)
        self.public_command_rate_limit_window = getattr(config, "PUBLIC_COMMAND_RATE_LIMIT_WINDOW", 30)
        self.public_command_rate_limit_max = getattr(config, "PUBLIC_COMMAND_RATE_LIMIT_MAX", 3)

        self.version_check_enabled = getattr(config, "VERSION_CHECK_ENABLED", False)
        self.version_check_interval = getattr(config, "VERSION_CHECK_INTERVAL", 3600)
        self.version_check_url = str(getattr(config, "VERSION_CHECK_URL", "")).strip() or None

        self.rtbl_announce = getattr(config, "RTBL_ANNOUNCE", True)
        self.rtbl_refresh_interval = getattr(config, "RTBL_REFRESH_INTERVAL", 3600)

        self.redaction_enabled = getattr(config, "REDACTION_ENABLED", False)
        self.redaction_index_retention_days = getattr(config, "REDACTION_INDEX_RETENTION_DAYS", 30)
        self.redaction_auto_reasons = list(getattr(config, "REDACTION_AUTO_REASONS", []))


    # --- Runtime config file editing helpers ---
    CONFIG_SECRET_KEYS = {"PASSWORD"}
    CONFIG_NEVER_WRITABLE_KEYS = set(STARTUP_ONLY_CONFIG_KEYS) | CONFIG_SECRET_KEYS

    def _config_file_path(self) -> pathlib.Path:
        path = getattr(config, "__file__", None)
        if path:
            return pathlib.Path(path).resolve()
        return pathlib.Path("config.py").resolve()

    def _config_sample_path(self) -> pathlib.Path:
        return pathlib.Path(__file__).resolve().parent.parent / "config_sample.py"

    def _ordered_config_keys_from_sample(self) -> list[str]:
        """Return config keys in config.py order, with config_sample.py as fallback.

        The active config.py order is preferred because that is what admins edit.
        Any keys missing from config.py are appended in config_sample.py order so
        !config show still gives a complete view of supported options.
        """
        keys: list[str] = []

        def add_keys_from(path: pathlib.Path) -> None:
            try:
                tree = ast.parse(path.read_text(encoding="utf8"))
            except Exception:
                return
            for node in tree.body:
                if isinstance(node, ast.Assign):
                    for target in node.targets:
                        if isinstance(target, ast.Name) and target.id.isupper() and target.id not in keys:
                            keys.append(target.id)
                elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                    if node.target.id.isupper() and node.target.id not in keys:
                        keys.append(node.target.id)

        add_keys_from(self._config_file_path())
        add_keys_from(self._config_sample_path())

        if not keys:
            return list(self.STARTUP_ONLY_CONFIG_KEYS) + list(self.CONFIG_KEYS)
        return keys

    def _config_default_values_from_sample(self) -> dict[str, Any]:
        defaults: dict[str, Any] = {}
        try:
            tree = ast.parse(self._config_sample_path().read_text(encoding="utf8"))
        except Exception:
            return defaults
        for node in tree.body:
            if isinstance(node, ast.Assign):
                try:
                    value = ast.literal_eval(node.value)
                except Exception:
                    continue
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id.isupper():
                        defaults[target.id] = value
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                try:
                    defaults[node.target.id] = ast.literal_eval(node.value)
                except Exception:
                    continue
        return defaults

    def get_ordered_config_items(self) -> list[tuple[str, Any, bool]]:
        """Return config values in config_sample.py order as (key, value, writable)."""
        keys = self._ordered_config_keys_from_sample()
        # Include custom/runtime keys even if an older config_sample.py was copied.
        for key in (*self.CONFIG_KEYS, *self.STARTUP_ONLY_CONFIG_KEYS):
            if key not in keys:
                keys.append(key)

        items: list[tuple[str, Any, bool]] = []
        for key in keys:
            if not key.isupper() or key == "RESSOURCE":
                continue
            value = get_config_resource() if key == "RESOURCE" else getattr(config, key, None)
            writable = key in self.CONFIG_KEYS and key not in self.CONFIG_NEVER_WRITABLE_KEYS
            items.append((key, value, writable))
        return items

    def format_config_value_for_display(self, key: str, value: Any) -> str:
        if key in self.CONFIG_SECRET_KEYS or any(token in key for token in ("PASSWORD", "SECRET", "TOKEN")):
            return "****" if value not in (None, "") else "None"
        return repr(value)

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

    def update_config_file_assignment(self, key: str, value: Any) -> None:
        path = self._config_file_path()
        text = path.read_text(encoding="utf8")
        assignment = self.render_config_assignment(key, value)
        pattern = re.compile(rf"^(?P<prefix>\s*){re.escape(key)}\s*=.*$", re.MULTILINE)
        if pattern.search(text):
            text = pattern.sub(lambda m: m.group("prefix") + assignment, text, count=1)
        else:
            text = text.rstrip() + "\n\n# Runtime config edits\n" + assignment + "\n"

        tmp_path = path.with_name(f".{path.name}.tmp-{os.getpid()}")
        with open(tmp_path, "w", encoding="utf8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
        try:
            dir_fd = os.open(path.parent, os.O_DIRECTORY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except OSError as exc:
            log.debug("Failed to fsync config directory %s: %s", path.parent, exc)

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

        try:
            self.update_config_file_assignment(key, new_value)
            importlib.reload(config)
            self.apply_runtime_config()
            await self.update_vcard()
        except Exception as exc:
            self._restore_config_values(previous_module_values)
            return False, f"Failed to write/apply config: {format_config_import_error(exc) if isinstance(exc, BaseException) else exc}"

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
