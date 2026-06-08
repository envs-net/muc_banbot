"""Runtime configuration package."""

from .display import ConfigDisplayMixin
from .imports import format_config_import_error, get_config_resource
from .runtime import ConfigRuntimeMixin
from .snapshots import ConfigSnapshotMixin
from .validation import ConfigValidationMixin


class ConfigMixin(
    ConfigRuntimeMixin,
    ConfigDisplayMixin,
    ConfigValidationMixin,
    ConfigSnapshotMixin,
):
    CONFIG_KEYS = (
        "LOG_LEVEL",
        "COMMAND_PREFIX",
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
        "ROOM_INVITE_MAX_AGE_DAYS",
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
        "LIST_PAGE_SIZE",
        "CONFIG_OUTPUT_MODE",
        "HELP_OUTPUT_MODE",
        "MUC_WRITE_SEMAPHORE",
        "SYNC_BATCH_SIZE",
        "STRUCTURED_EVENT_LOGS",
        "AUDIT_LOG_ENABLED",
        "AUDIT_LOG_RETENTION_DAYS",
        "RTBL_ANNOUNCE",
        "RTBL_REFRESH_INTERVAL",
        "REDACTION_ENABLED",
        "REDACTION_INDEX_RETENTION_DAYS",
        "AUTO_REDACT_ON_IMPORTED_BAN_REASON",
        "AUTO_REDACT_ON_MANUAL_MUC_BAN",
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

    CONFIG_SECRET_KEYS = {"PASSWORD"}

    STARTUP_ONLY_CONFIG_KEYS = (
        "JID",
        "PASSWORD",
        "RESOURCE",
        "RESSOURCE",
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

    CONFIG_NEVER_WRITABLE_KEYS = set(STARTUP_ONLY_CONFIG_KEYS) | CONFIG_SECRET_KEYS

    """Combined configuration mixin."""


__all__ = ["ConfigMixin", "format_config_import_error", "get_config_resource"]
