"""Configuration validation helpers."""

from __future__ import annotations

import importlib.util
import logging
import pathlib

import config

from ..utils import validate_jid_format

log = logging.getLogger(__name__)

class ConfigValidationMixin:

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

        for key in ("CONFIG_OUTPUT_MODE", "HELP_OUTPUT_MODE"):
            mode = str(getattr(config, key, "all")).lower().strip()
            if mode not in {"all", "paginate"}:
                errors.append(f"{key} must be one of all, paginate")

        int_ranges = {
            "AUDIT_LOG_RETENTION_DAYS": (1, 365),
            "HEALTH_CHECK_INTERVAL": (60, 86400),
            "UNBAN_CHECK_INTERVAL": (10, 86400),
            "MAX_TEMPBAN_DAYS": (1, 365),
            "PUBLIC_COMMAND_RATE_LIMIT_WINDOW": (1, 3600),
            "PUBLIC_COMMAND_RATE_LIMIT_MAX": (1, 100),
            "LIST_PAGE_SIZE": (1, 100),
            "ROOM_INVITE_MAX_AGE_DAYS": (0, 3650),
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
            "ROOM_INVITE_MAX_AGE_DAYS": 30,
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
        if not isinstance(getattr(config, "AUTO_REDACT_ON_IMPORTED_BAN_REASON", False), bool):
            errors.append("AUTO_REDACT_ON_IMPORTED_BAN_REASON must be True or False")
        if not isinstance(getattr(config, "AUTO_REDACT_ON_MANUAL_MUC_BAN", False), bool):
            errors.append("AUTO_REDACT_ON_MANUAL_MUC_BAN must be True or False")
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
