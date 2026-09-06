"""Declarative configuration metadata for muc_banbot."""

from __future__ import annotations

from envs_xmpp_core.config.schema import MISSING, ConfigKeySpec


def _runtime(
    default: object,
    name: str,
    accepted_type: type | tuple[type, ...],
    **kwargs: object,
) -> ConfigKeySpec:
    return ConfigKeySpec(
        default,
        name,
        accepted_type,
        runtime_writable=True,
        **kwargs,
    )


def _startup(
    default: object,
    name: str,
    accepted_type: type | tuple[type, ...],
    **kwargs: object,
) -> ConfigKeySpec:
    return ConfigKeySpec(
        default,
        name,
        accepted_type,
        startup_only=True,
        **kwargs,
    )


CONFIG_FIELDS: dict[str, ConfigKeySpec] = {
    # Startup-only identity and connection settings.
    "JID": _startup(MISSING, "JID", str, required=True, sample="adminbot@domain.tld"),
    "RESOURCE": _startup("service", "RESOURCE", str),
    "RESSOURCE": _startup(MISSING, "RESSOURCE", str),
    "PASSWORD": _startup(
        MISSING,
        "PASSWORD",
        str,
        required=True,
        sensitive=True,
        sample="yourpassword",
    ),
    "ADMIN_ROOM": _startup(
        MISSING,
        "ADMIN_ROOM",
        str,
        required=True,
        sample="admin@muc.domain.tld",
    ),
    "NICK": _startup(MISSING, "NICK", str, required=True, sample="adminbot"),
    "DB_FILE": _startup(MISSING, "DB_FILE", str, required=True, sample="banbot.db"),
    "CONNECT_HOST": _startup(None, "CONNECT_HOST", (str, type(None)), allow_empty=True),
    "CONNECT_PORT": _startup(5222, "CONNECT_PORT", int, minimum=1, maximum=65535),
    "CONNECT_DIRECT_TLS": _startup(False, "CONNECT_DIRECT_TLS", bool),
    "WATCHDOG_ENABLED": _startup(True, "WATCHDOG_ENABLED", bool),
    "WATCHDOG_INTERVAL_SECONDS": _startup(
        20, "WATCHDOG_INTERVAL_SECONDS", (int, float), minimum=1.0, maximum=300.0
    ),
    "WATCHDOG_LAG_WARNING_SECONDS": _startup(
        2.0, "WATCHDOG_LAG_WARNING_SECONDS", (int, float), minimum=0.1, maximum=300.0
    ),
    "WATCHDOG_LAG_FAILURE_SECONDS": _startup(
        30.0, "WATCHDOG_LAG_FAILURE_SECONDS", (int, float), minimum=0.1, maximum=600.0
    ),
    "RTBL_ENABLED": _startup(False, "RTBL_ENABLED", bool),
    "RTBL_PUBLISH_ENABLED": _startup(False, "RTBL_PUBLISH_ENABLED", bool),
    "RTBL_PUBLISH_SERVICE": _startup("pubsub.domain.tld", "RTBL_PUBLISH_SERVICE", str),
    "RTBL_PUBLISH_JID_NODE": _startup("muc_bans_sha256", "RTBL_PUBLISH_JID_NODE", str),
    "RTBL_PUBLISH_DOMAIN_NODE": _startup("muc_bans_domains", "RTBL_PUBLISH_DOMAIN_NODE", str),
    "OMEMO_ENABLED": _startup(False, "OMEMO_ENABLED", bool),
    "OMEMO_STORAGE_FILE": _startup("data/omemo.json", "OMEMO_STORAGE_FILE", str),
    "OMEMO_AUTO_ENCRYPT_ADMIN_ROOM": _startup(True, "OMEMO_AUTO_ENCRYPT_ADMIN_ROOM", bool),
    "OMEMO_PLAINTEXT_FALLBACK": _startup(False, "OMEMO_PLAINTEXT_FALLBACK", bool),
    "OMEMO_RESET_ON_IDENTITY_CHANGE": _startup(True, "OMEMO_RESET_ON_IDENTITY_CHANGE", bool),
    # Runtime-writable settings.
    "LOG_LEVEL": _runtime(
        "INFO",
        "LOG_LEVEL",
        str,
        choices=("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"),
    ),
    "COMMAND_PREFIX": _runtime("!", "COMMAND_PREFIX", str, allow_empty=True),
    "DB_BACKUP_ON_START": _runtime(True, "DB_BACKUP_ON_START", bool),
    "DB_BACKUP_DIR": _runtime("data/backups", "DB_BACKUP_DIR", str),
    "DB_BACKUP_KEEP": _runtime(15, "DB_BACKUP_KEEP", int, minimum=1, maximum=1000),
    "DB_BACKUP_INCLUDE_OMEMO": _runtime(True, "DB_BACKUP_INCLUDE_OMEMO", bool),
    "EXPORT_DIR": _runtime("data/exports", "EXPORT_DIR", str),
    "EXPORT_KEEP": _runtime(15, "EXPORT_KEEP", int, minimum=1, maximum=1000),
    "ANNOUNCE_STARTUP": _runtime(True, "ANNOUNCE_STARTUP", bool),
    "ANNOUNCE_SYNC_DETAILS": _runtime(True, "ANNOUNCE_SYNC_DETAILS", bool),
    "SHOW_BAN_IN_MUC": _runtime(False, "SHOW_BAN_IN_MUC", bool),
    "ALLOW_USER_COMMANDS_IN_PROTECTED_ROOMS": _runtime(
        True, "ALLOW_USER_COMMANDS_IN_PROTECTED_ROOMS", bool
    ),
    "ALLOW_ADMIN_COMMANDS_IN_DMS": _runtime(True, "ALLOW_ADMIN_COMMANDS_IN_DMS", bool),
    "ROOM_INVITES_ENABLED": _runtime(False, "ROOM_INVITES_ENABLED", bool),
    "ROOM_INVITE_MAX_AGE_DAYS": _runtime(
        30, "ROOM_INVITE_MAX_AGE_DAYS", int, minimum=0, maximum=3650
    ),
    "ALERT_ON_RECONNECT": _runtime(True, "ALERT_ON_RECONNECT", bool),
    "ALERT_ON_ADMIN_RIGHTS_LOST": _runtime(True, "ALERT_ON_ADMIN_RIGHTS_LOST", bool),
    "ALERT_ON_HEALTH_CHECK_FAILURE": _runtime(True, "ALERT_ON_HEALTH_CHECK_FAILURE", bool),
    "ALERT_ON_DB_STATS_FAILURE": _runtime(True, "ALERT_ON_DB_STATS_FAILURE", bool),
    "ALERT_ON_REDACTION_FAILURE": _runtime(True, "ALERT_ON_REDACTION_FAILURE", bool),
    "ALERT_ON_DB_SIZE_MB": _runtime(0, "ALERT_ON_DB_SIZE_MB", int, minimum=0, maximum=1048576),
    "ALERT_ON_RTBL_REFRESH_FAILURES": _runtime(
        3, "ALERT_ON_RTBL_REFRESH_FAILURES", int, minimum=0, maximum=1000
    ),
    "ALERT_DEDUP_WINDOW": _runtime(300, "ALERT_DEDUP_WINDOW", int, minimum=0, maximum=86400),
    "HEALTH_CHECK_INTERVAL": _runtime(
        300, "HEALTH_CHECK_INTERVAL", int, minimum=60, maximum=86400
    ),
    "MUC_JOIN_TIMEOUT_SECONDS": _runtime(
        20, "MUC_JOIN_TIMEOUT_SECONDS", int, minimum=5, maximum=300
    ),
    "MUC_JOIN_RETRIES": _runtime(2, "MUC_JOIN_RETRIES", int, minimum=1, maximum=10),
    "UNBAN_CHECK_INTERVAL": _runtime(
        60, "UNBAN_CHECK_INTERVAL", int, minimum=10, maximum=86400
    ),
    "MAX_TEMPBAN_DAYS": _runtime(30, "MAX_TEMPBAN_DAYS", int, minimum=1, maximum=365),
    "PUBLIC_COMMAND_RATE_LIMIT_WINDOW": _runtime(
        10, "PUBLIC_COMMAND_RATE_LIMIT_WINDOW", int, minimum=1, maximum=3600
    ),
    "PUBLIC_COMMAND_RATE_LIMIT_MAX": _runtime(
        3, "PUBLIC_COMMAND_RATE_LIMIT_MAX", int, minimum=1, maximum=100
    ),
    "LIST_PAGE_SIZE": _runtime(10, "LIST_PAGE_SIZE", int, minimum=1, maximum=100),
    "CONFIG_OUTPUT_MODE": _runtime(
        "all", "CONFIG_OUTPUT_MODE", str, choices=("all", "paginate")
    ),
    "HELP_OUTPUT_MODE": _runtime(
        "all", "HELP_OUTPUT_MODE", str, choices=("all", "paginate")
    ),
    "MUC_WRITE_SEMAPHORE": _runtime(5, "MUC_WRITE_SEMAPHORE", int, minimum=1, maximum=100),
    "SYNC_BATCH_SIZE": _runtime(10, "SYNC_BATCH_SIZE", int, minimum=1, maximum=100),
    "STRUCTURED_EVENT_LOGS": _runtime(True, "STRUCTURED_EVENT_LOGS", bool),
    "AUDIT_LOG_ENABLED": _runtime(True, "AUDIT_LOG_ENABLED", bool),
    "AUDIT_LOG_RETENTION_DAYS": _runtime(
        365, "AUDIT_LOG_RETENTION_DAYS", int, minimum=1, maximum=365
    ),
    "RTBL_ANNOUNCE": _runtime(True, "RTBL_ANNOUNCE", bool),
    "RTBL_REFRESH_INTERVAL": _runtime(3600, "RTBL_REFRESH_INTERVAL", int, minimum=0),
    "REDACTION_ENABLED": _runtime(False, "REDACTION_ENABLED", bool),
    "REDACTION_INDEX_RETENTION_DAYS": _runtime(
        30, "REDACTION_INDEX_RETENTION_DAYS", int, minimum=0
    ),
    "REDACTION_RETRACT_CONCURRENCY": _runtime(
        10, "REDACTION_RETRACT_CONCURRENCY", int, minimum=1, maximum=20
    ),
    "REDACTION_IQ_TIMEOUT_SECONDS": _runtime(
        5, "REDACTION_IQ_TIMEOUT_SECONDS", (int, float), minimum=1, maximum=30
    ),
    "AUTO_REDACT_ON_IMPORTED_BAN_REASON": _runtime(
        False, "AUTO_REDACT_ON_IMPORTED_BAN_REASON", bool
    ),
    "AUTO_REDACT_ON_MANUAL_MUC_BAN": _runtime(True, "AUTO_REDACT_ON_MANUAL_MUC_BAN", bool),
    "REDACTION_AUTO_REASONS": _runtime(
        [],
        "REDACTION_AUTO_REASONS",
        (list, tuple),
        sample=[
            "code of conduct violations",
            "open-reg",
            "spam",
            "advertising",
            "impersonation",
            "disagreement",
            "harassment",
            "hate speech",
            "doxxing",
            "violence",
            "terrorism",
            "csam",
            "gore",
            "troll",
            "racist",
            "cp",
            "nsfw",
        ],
    ),
    "VERSION_CHECK_ENABLED": _runtime(False, "VERSION_CHECK_ENABLED", bool),
    "VERSION_CHECK_INTERVAL": _runtime(
        3600, "VERSION_CHECK_INTERVAL", int, minimum=300, maximum=86400
    ),
    "VERSION_CHECK_URL": _runtime(
        "https://github.com/envs-net/muc_banbot/releases/latest",
        "VERSION_CHECK_URL",
        str,
        allow_empty=True,
    ),
    "AVATAR_PATH": _runtime("avatar.png", "AVATAR_PATH", (str, type(None)), allow_empty=True),
    "VCARD_NICKNAME": _runtime("My Bot Nickname", "VCARD_NICKNAME", (str, type(None)), allow_empty=True),
    "VCARD_FN": _runtime("Admin Bot", "VCARD_FN", (str, type(None)), allow_empty=True),
    "VCARD_ORG": _runtime("My Organization", "VCARD_ORG", (str, type(None)), allow_empty=True),
    "VCARD_ROLE": _runtime("Administrator", "VCARD_ROLE", (str, type(None)), allow_empty=True),
    "VCARD_URL": _runtime("https://example.com", "VCARD_URL", (str, type(None)), allow_empty=True),
    "VCARD_NOTE": _runtime("Bot Admin Assistant", "VCARD_NOTE", (str, type(None)), allow_empty=True),
}
