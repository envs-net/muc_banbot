"""Configuration validation helpers."""

from __future__ import annotations

import importlib.util
import logging
import os
import pathlib
from typing import cast

from envs_xmpp_core.config.schema import MISSING, matches_expected_type

import config

from ..utils import validate_jid_format
from .spec import CONFIG_FIELDS

log = logging.getLogger(__name__)

class ConfigValidationMixin:

    def _validate_config(self) -> tuple[list[str], list[str]]:
        """Validate config.py and return (errors, warnings)."""
        errors: list[str] = []
        warnings: list[str] = []

        def config_value(name: str, default: object) -> object:
            value = getattr(config, name, default)
            return default if value is None else value

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

        resource = getattr(config, "RESOURCE", None)
        legacy_resource = getattr(config, "RESSOURCE", None)
        if (
            resource is not None
            and legacy_resource is not None
            and resource != legacy_resource
        ):
            warnings.append("Both RESOURCE and legacy RESSOURCE are set; RESOURCE will be used")

        log_level = str(config_value("LOG_LEVEL", "INFO")).upper().strip()
        log_level_choices = CONFIG_FIELDS["LOG_LEVEL"].choices
        if log_level not in log_level_choices:
            errors.append(
                "LOG_LEVEL must be one of " + ", ".join(log_level_choices)
            )

        command_prefix = str(config_value("COMMAND_PREFIX", "!")).strip()
        if not command_prefix:
            warnings.append("COMMAND_PREFIX is empty; effective value will be '!'")
        elif any(ch.isspace() for ch in command_prefix):
            errors.append("COMMAND_PREFIX must not contain whitespace")

        for key in ("CONFIG_OUTPUT_MODE", "HELP_OUTPUT_MODE"):
            field = CONFIG_FIELDS[key]
            mode = str(config_value(key, field.default)).lower().strip()
            if mode not in field.choices:
                errors.append(f"{key} must be one of {', '.join(field.choices)}")

        # Type/lifecycle metadata is declared once in banbot.config.spec.  Keep
        # bot-specific wording here while deriving the validated keys/defaults
        # from that shared schema instead of maintaining parallel lists.
        special_int_validation = {
            "CONNECT_PORT",
            "REDACTION_INDEX_RETENTION_DAYS",
            "REDACTION_RETRACT_CONCURRENCY",
            "RTBL_REFRESH_INTERVAL",
        }
        for name, field in CONFIG_FIELDS.items():
            default = field.default
            if default is MISSING:
                continue
            value = config_value(name, default)

            if field.accepted_type is bool:
                if not matches_expected_type(value, bool):
                    errors.append(f"{name} must be True or False")
                continue

            if (
                field.accepted_type is int
                and name not in special_int_validation
                and (field.minimum is not None or field.maximum is not None)
            ):
                if not matches_expected_type(value, int):
                    errors.append(f"{name} must be an integer")
                    continue
                if field.minimum is not None and value < field.minimum:
                    if field.maximum is not None:
                        errors.append(
                            f"{name} must be between {field.minimum} and {field.maximum} (got {value})"
                        )
                    else:
                        errors.append(f"{name} must be at least {field.minimum} (got {value})")
                    continue
                if field.maximum is not None and value > field.maximum:
                    if field.minimum is not None:
                        errors.append(
                            f"{name} must be between {field.minimum} and {field.maximum} (got {value})"
                        )
                    else:
                        errors.append(f"{name} must be at most {field.maximum} (got {value})")

        for name in (
            "WATCHDOG_INTERVAL_SECONDS",
            "WATCHDOG_LAG_WARNING_SECONDS",
            "WATCHDOG_LAG_FAILURE_SECONDS",
        ):
            field = CONFIG_FIELDS[name]
            value = config_value(name, field.default)
            if not matches_expected_type(value, field.accepted_type):
                errors.append(f"{name} must be a number")
            elif (
                field.minimum is not None
                and field.maximum is not None
                and not field.minimum <= float(cast(int | float, value)) <= field.maximum
            ):
                errors.append(
                    f"{name} must be between {field.minimum:g} and {field.maximum:g}"
                )

        watchdog_warning = config_value("WATCHDOG_LAG_WARNING_SECONDS", 2.0)
        watchdog_failure = config_value("WATCHDOG_LAG_FAILURE_SECONDS", 30.0)
        if (
            isinstance(watchdog_warning, (int, float))
            and not isinstance(watchdog_warning, bool)
            and isinstance(watchdog_failure, (int, float))
            and not isinstance(watchdog_failure, bool)
            and watchdog_failure < watchdog_warning
        ):
            errors.append("WATCHDOG_LAG_FAILURE_SECONDS must be >= WATCHDOG_LAG_WARNING_SECONDS")

        version_url = str(config_value("VERSION_CHECK_URL", "")).strip()
        if config_value("VERSION_CHECK_ENABLED", False) and not version_url:
            errors.append("VERSION_CHECK_URL must not be empty when VERSION_CHECK_ENABLED=True")
        if version_url and not version_url.startswith(("http://", "https://")):
            errors.append("VERSION_CHECK_URL must start with http:// or https://")

        config_path_value = getattr(config, "__file__", None)
        if config_path_value:
            config_path = pathlib.Path(str(config_path_value))
            try:
                config_stat = config_path.stat()
            except OSError as exc:
                warnings.append(f"Could not inspect config.py permissions: {exc}")
            else:
                config_mode = config_stat.st_mode & 0o777
                if config_mode & 0o077:
                    warnings.append(
                        f"config.py permissions are too open ({config_mode:04o}); expected 0600 because it contains credentials"
                    )
                if hasattr(os, "geteuid") and config_stat.st_uid != os.geteuid():
                    warnings.append(
                        "config.py is not owned by the running service user; runtime !config edits may fail"
                    )

        avatar_path = config_value("AVATAR_PATH", None)
        if avatar_path and not pathlib.Path(str(avatar_path)).exists():
            warnings.append(f"AVATAR_PATH does not exist: {avatar_path}")

        if db_file:
            db_parent = pathlib.Path(db_file).expanduser().parent
            if str(db_parent) not in ("", ".") and not db_parent.exists():
                errors.append(f"DB_FILE directory does not exist: {db_parent}")

        backup_dir_value = config_value("DB_BACKUP_DIR", "data/backups")
        if not isinstance(backup_dir_value, str):
            errors.append("DB_BACKUP_DIR must be a string")
            backup_dir = ""
        else:
            backup_dir = backup_dir_value.strip()
        if not backup_dir:
            errors.append("DB_BACKUP_DIR must not be empty")

        export_dir_value = config_value("EXPORT_DIR", "data/exports")
        if not isinstance(export_dir_value, str):
            errors.append("EXPORT_DIR must be a string")
            export_dir = ""
        else:
            export_dir = export_dir_value.strip()
        if not export_dir:
            errors.append("EXPORT_DIR must not be empty")

        # --- Connection ---
        connect_host = config_value("CONNECT_HOST", None)
        if connect_host is not None and not isinstance(connect_host, str):
            errors.append("CONNECT_HOST must be a string or None")

        connect_port = config_value("CONNECT_PORT", 5222)
        if not isinstance(connect_port, int) or not (1 <= connect_port <= 65535):
            errors.append("CONNECT_PORT must be an integer between 1 and 65535")

        # --- RTBL ---
        rtbl_refresh = config_value("RTBL_REFRESH_INTERVAL", 3600)
        if not isinstance(rtbl_refresh, int) or rtbl_refresh < 0:
            errors.append("RTBL_REFRESH_INTERVAL must be a non-negative integer (0 = disabled)")

        # --- Redaction ---
        redaction_retention = config_value("REDACTION_INDEX_RETENTION_DAYS", 30)
        if not isinstance(redaction_retention, int) or redaction_retention < 0:
            errors.append("REDACTION_INDEX_RETENTION_DAYS must be a non-negative integer (0 = keep forever)")
        redaction_concurrency = config_value("REDACTION_RETRACT_CONCURRENCY", 10)
        if not isinstance(redaction_concurrency, int) or isinstance(redaction_concurrency, bool) or not 1 <= redaction_concurrency <= 20:
            errors.append("REDACTION_RETRACT_CONCURRENCY must be an integer between 1 and 20")
        redaction_timeout = config_value("REDACTION_IQ_TIMEOUT_SECONDS", 5)
        if not isinstance(redaction_timeout, (int, float)) or isinstance(redaction_timeout, bool) or not 1 <= redaction_timeout <= 30:
            errors.append("REDACTION_IQ_TIMEOUT_SECONDS must be a number between 1 and 30")
        redaction_reasons = config_value("REDACTION_AUTO_REASONS", [])
        if not isinstance(redaction_reasons, (list, tuple)) or not all(isinstance(item, str) for item in redaction_reasons):
            errors.append("REDACTION_AUTO_REASONS must be a list of strings")

        # --- RTBL Publish ---
        rtbl_pub = config_value("RTBL_PUBLISH_ENABLED", False)
        if rtbl_pub:
            pub_service = str(config_value("RTBL_PUBLISH_SERVICE", "")).strip()
            pub_jid_node = str(config_value("RTBL_PUBLISH_JID_NODE", "")).strip()
            pub_domain_node = str(config_value("RTBL_PUBLISH_DOMAIN_NODE", "")).strip()
            if not pub_service:
                errors.append("RTBL_PUBLISH_SERVICE must not be empty when RTBL_PUBLISH_ENABLED=True")
            elif "." not in pub_service:
                errors.append("RTBL_PUBLISH_SERVICE must be a valid JID like pubsub.domain.tld")
            if not pub_jid_node:
                errors.append("RTBL_PUBLISH_JID_NODE must not be empty when RTBL_PUBLISH_ENABLED=True")
            if not pub_domain_node:
                errors.append("RTBL_PUBLISH_DOMAIN_NODE must not be empty when RTBL_PUBLISH_ENABLED=True")

        # --- OMEMO ---
        omemo_enabled = config_value("OMEMO_ENABLED", False)
        if omemo_enabled:
            if importlib.util.find_spec("slixmpp_omemo") is None or importlib.util.find_spec("omemo") is None:
                warnings.append(
                    "OMEMO_ENABLED=True but optional OMEMO dependencies are not installed; "
                    "the bot will start with OMEMO disabled. Install requirements-omemo.txt "
                    "after installing system libraries such as libsodium-dev and libxeddsa-dev."
                )

            omemo_storage_raw = str(config_value("OMEMO_STORAGE_FILE", "data/omemo.json")).strip()
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
