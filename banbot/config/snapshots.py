"""Runtime configuration snapshot helpers."""

from __future__ import annotations

import logging

import config

from .imports import get_config_resource

log = logging.getLogger(__name__)

class ConfigSnapshotMixin:

    def _runtime_config_snapshot(self) -> dict[str, object]:
        """Return the currently effective runtime config values."""
        return {
            "LOG_LEVEL": getattr(self, "log_level", str(getattr(config, "LOG_LEVEL", "INFO")).upper()),
            "COMMAND_PREFIX": self.command_prefix,
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
            "ROOM_INVITE_MAX_AGE_DAYS": getattr(self, "room_invite_max_age_days", 30),
            "ALERT_ON_RECONNECT": getattr(self, "alert_on_reconnect", True),
            "ALERT_ON_ADMIN_RIGHTS_LOST": getattr(self, "alert_on_admin_rights_lost", True),
            "ALERT_ON_HEALTH_CHECK_FAILURE": getattr(self, "alert_on_health_check_failure", True),
            "ALERT_ON_DB_STATS_FAILURE": getattr(self, "alert_on_db_stats_failure", True),
            "ALERT_ON_REDACTION_FAILURE": getattr(self, "alert_on_redaction_failure", True),
            "ALERT_ON_DB_SIZE_MB": getattr(self, "alert_on_db_size_mb", 0),
            "ALERT_ON_RTBL_REFRESH_FAILURES": getattr(self, "alert_on_rtbl_refresh_failures", 3),
            "ALERT_DEDUP_WINDOW": getattr(self, "alert_dedup_window", 300),
            "HEALTH_CHECK_INTERVAL": self.health_check_interval,
            "MUC_JOIN_TIMEOUT_SECONDS": getattr(self, "muc_join_timeout_seconds", 20),
            "MUC_JOIN_RETRIES": getattr(self, "muc_join_retries", 2),
            "UNBAN_CHECK_INTERVAL": self.unban_check_interval,
            "MAX_TEMPBAN_DAYS": self.max_tempban_days,
            "PUBLIC_COMMAND_RATE_LIMIT_WINDOW": self.public_command_rate_limit_window,
            "PUBLIC_COMMAND_RATE_LIMIT_MAX": self.public_command_rate_limit_max,
            "LIST_PAGE_SIZE": getattr(self, "list_page_size", 10),
            "CONFIG_OUTPUT_MODE": getattr(self, "config_output_mode", "all"),
            "HELP_OUTPUT_MODE": getattr(self, "help_output_mode", "all"),
            "MUC_WRITE_SEMAPHORE": self.muc_write_limit,
            "SYNC_BATCH_SIZE": getattr(self, "sync_batch_size", 10),
            "STRUCTURED_EVENT_LOGS": self.structured_event_logs,
            "AUDIT_LOG_ENABLED": self.audit_log_enabled,
            "AUDIT_LOG_RETENTION_DAYS": self.audit_log_retention_days,
            "RTBL_ANNOUNCE": self.rtbl_announce,
            "RTBL_REFRESH_INTERVAL": self.rtbl_refresh_interval,
            "REDACTION_ENABLED": self.redaction_enabled,
            "REDACTION_INDEX_RETENTION_DAYS": self.redaction_index_retention_days,
            "REDACTION_RETRACT_CONCURRENCY": getattr(self, "redaction_retract_concurrency", 10),
            "REDACTION_IQ_TIMEOUT_SECONDS": getattr(self, "redaction_iq_timeout_seconds", 5),
            "AUTO_REDACT_ON_IMPORTED_BAN_REASON": getattr(self, "auto_redact_on_imported_ban_reason", False),
            "AUTO_REDACT_ON_MANUAL_MUC_BAN": getattr(self, "auto_redact_on_manual_muc_ban", True),
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
