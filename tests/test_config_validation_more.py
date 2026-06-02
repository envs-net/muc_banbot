"""Additional config validation and reload helper coverage."""

from __future__ import annotations

import logging

import config
from banbot.config_utils import ConfigMixin, format_config_import_error, get_config_resource


class ConfigValidationBot(ConfigMixin):
    def __init__(self):
        self.command_prefix = "!"
        self.announce_startup = True
        self.announce_sync_details = True
        self.show_ban_in_muc = False
        self.allow_user_cmds = True
        self.allow_admin_commands_in_dms = True
        self.room_invites_enabled = False
        self.health_check_interval = 300
        self.unban_check_interval = 60
        self.max_tempban_days = 30
        self.public_command_rate_limit_window = 30
        self.public_command_rate_limit_max = 3
        self.muc_write_limit = 5
        self.sync_batch_size = 10
        self.structured_event_logs = True
        self.audit_log_enabled = True
        self.audit_log_retention_days = 365
        self.rtbl_announce = True
        self.version_check_enabled = False
        self.version_check_interval = 3600
        self.version_check_url = None
        self.log_level = "INFO"


def set_valid_config(monkeypatch):
    values = {
        "JID": "bot@example.org",
        "PASSWORD": "secret",
        "ADMIN_ROOM": "admin@conference.example.org",
        "NICK": "BanBot",
        "DB_FILE": "banbot.sqlite3",
        "CONNECT_HOST": None,
        "CONNECT_PORT": 5222,
        "CONNECT_DIRECT_TLS": False,
        "LOG_LEVEL": "INFO",
        "COMMAND_PREFIX": "!",
        "AUDIT_LOG_RETENTION_DAYS": 365,
        "HEALTH_CHECK_INTERVAL": 300,
        "ALLOW_ADMIN_COMMANDS_IN_DMS": True,
        "ROOM_INVITES_ENABLED": False,
        "ROOM_INVITE_MAX_AGE_DAYS": 30,
        "UNBAN_CHECK_INTERVAL": 60,
        "MAX_TEMPBAN_DAYS": 30,
        "PUBLIC_COMMAND_RATE_LIMIT_WINDOW": 30,
        "PUBLIC_COMMAND_RATE_LIMIT_MAX": 3,
        "LIST_PAGE_SIZE": 10,
        "MUC_WRITE_SEMAPHORE": 5,
        "SYNC_BATCH_SIZE": 10,
        "VERSION_CHECK_INTERVAL": 3600,
        "RTBL_REFRESH_INTERVAL": 3600,
        "REDACTION_ENABLED": False,
        "REDACTION_INDEX_RETENTION_DAYS": 30,
        "REDACTION_AUTO_REASONS": [],
        "RTBL_ENABLED": False,
        "RTBL_PUBLISH_ENABLED": False,
        "OMEMO_ENABLED": False,
        "OMEMO_AUTO_ENCRYPT_ADMIN_ROOM": False,
        "OMEMO_PLAINTEXT_FALLBACK": False,
    }
    for key, value in values.items():
        monkeypatch.setattr(config, key, value, raising=False)


def test_validate_config_accepts_valid_values(monkeypatch):
    set_valid_config(monkeypatch)
    bot = ConfigValidationBot()

    errors, warnings = bot._validate_config()

    assert errors == []
    assert warnings == []


def test_validate_config_reports_multiple_errors_and_placeholder_credentials(monkeypatch):
    set_valid_config(monkeypatch)
    monkeypatch.setattr(config, "JID", "not-a-jid", raising=False)
    monkeypatch.setattr(config, "PASSWORD", "changeme", raising=False)
    monkeypatch.setattr(config, "NICK", "Bad Nick", raising=False)
    monkeypatch.setattr(config, "COMMAND_PREFIX", "! bad", raising=False)
    monkeypatch.setattr(config, "LOG_LEVEL", "VERBOSE", raising=False)
    monkeypatch.setattr(config, "HEALTH_CHECK_INTERVAL", 1, raising=False)
    bot = ConfigValidationBot()

    errors, warnings = bot._validate_config()

    assert "JID must be a valid bare JID like bot@example.org" in errors
    assert "NICK must not contain whitespace" in errors
    assert "COMMAND_PREFIX must not contain whitespace" in errors
    assert "LOG_LEVEL must be one of DEBUG, INFO, WARNING, ERROR, CRITICAL" in errors
    assert any("HEALTH_CHECK_INTERVAL" in error for error in errors)
    assert "PASSWORD still looks like a placeholder" in errors


def test_validate_config_rejects_sample_config_identity_values(monkeypatch):
    set_valid_config(monkeypatch)
    monkeypatch.setattr(config, "JID", "adminbot@domain.tld", raising=False)
    monkeypatch.setattr(config, "PASSWORD", "yourpassword", raising=False)
    monkeypatch.setattr(config, "ADMIN_ROOM", "admin@muc.domain.tld", raising=False)

    bot = ConfigValidationBot()
    errors, warnings = bot._validate_config()

    assert "JID still looks like the sample config value" in errors
    assert "PASSWORD still looks like a placeholder" in errors
    assert "ADMIN_ROOM still looks like the sample config value" in errors
    assert "PASSWORD still looks like a placeholder" not in warnings


def test_validate_config_rejects_invalid_connection_settings(monkeypatch):
    set_valid_config(monkeypatch)
    monkeypatch.setattr(config, "CONNECT_HOST", 123, raising=False)
    monkeypatch.setattr(config, "CONNECT_PORT", 70000, raising=False)
    monkeypatch.setattr(config, "CONNECT_DIRECT_TLS", "yes", raising=False)

    bot = ConfigValidationBot()
    errors, _warnings = bot._validate_config()

    assert "CONNECT_HOST must be a string or None" in errors
    assert "CONNECT_PORT must be an integer between 1 and 65535" in errors
    assert "CONNECT_DIRECT_TLS must be True or False" in errors


def test_validate_config_rejects_invalid_redaction_settings(monkeypatch):
    set_valid_config(monkeypatch)
    monkeypatch.setattr(config, "REDACTION_ENABLED", "yes", raising=False)
    monkeypatch.setattr(config, "REDACTION_INDEX_RETENTION_DAYS", -1, raising=False)
    monkeypatch.setattr(config, "REDACTION_AUTO_REASONS", ["spam", 123], raising=False)

    bot = ConfigValidationBot()
    errors, _warnings = bot._validate_config()

    assert "REDACTION_ENABLED must be True or False" in errors
    assert "REDACTION_INDEX_RETENTION_DAYS must be a non-negative integer (0 = keep forever)" in errors
    assert "REDACTION_AUTO_REASONS must be a list of strings" in errors


def test_apply_runtime_config_updates_attributes_and_log_level(monkeypatch):
    set_valid_config(monkeypatch)
    monkeypatch.setattr(config, "COMMAND_PREFIX", ".", raising=False)
    monkeypatch.setattr(config, "ANNOUNCE_STARTUP", False, raising=False)
    monkeypatch.setattr(config, "RTBL_REFRESH_INTERVAL", 0, raising=False)
    monkeypatch.setattr(config, "LOG_LEVEL", "ERROR", raising=False)
    bot = ConfigValidationBot()

    bot.apply_runtime_config()

    assert bot.command_prefix == "."
    assert bot.announce_startup is False
    assert bot.allow_admin_commands_in_dms is True
    assert bot.room_invites_enabled is False
    assert bot.room_invite_max_age_days == 30
    assert bot.rtbl_refresh_interval == 0
    assert logging.getLogger("banbot").level == logging.ERROR




def test_validate_config_reports_invalid_rtbl_and_omemo_values(monkeypatch):
    set_valid_config(monkeypatch)
    monkeypatch.setattr(config, "ALLOW_ADMIN_COMMANDS_IN_DMS", "yes", raising=False)
    monkeypatch.setattr(config, "ROOM_INVITES_ENABLED", "yes", raising=False)
    monkeypatch.setattr(config, "ROOM_INVITE_MAX_AGE_DAYS", -1, raising=False)
    monkeypatch.setattr(config, "RTBL_ENABLED", "yes", raising=False)
    monkeypatch.setattr(config, "RTBL_ANNOUNCE", "yes", raising=False)
    monkeypatch.setattr(config, "RTBL_REFRESH_INTERVAL", -1, raising=False)
    monkeypatch.setattr(config, "RTBL_PUBLISH_ENABLED", "yes", raising=False)
    monkeypatch.setattr(config, "OMEMO_ENABLED", "yes", raising=False)
    monkeypatch.setattr(config, "OMEMO_AUTO_ENCRYPT_ADMIN_ROOM", "yes", raising=False)
    monkeypatch.setattr(config, "OMEMO_PLAINTEXT_FALLBACK", "yes", raising=False)
    bot = ConfigValidationBot()

    errors, _warnings = bot._validate_config()

    assert "ALLOW_ADMIN_COMMANDS_IN_DMS must be True or False" in errors
    assert "ROOM_INVITES_ENABLED must be True or False" in errors
    assert "RTBL_ENABLED must be True or False" in errors
    assert "RTBL_ANNOUNCE must be True or False" in errors
    assert "RTBL_REFRESH_INTERVAL must be a non-negative integer (0 = disabled)" in errors
    assert "RTBL_PUBLISH_ENABLED must be True or False" in errors
    assert "OMEMO_ENABLED must be True or False" in errors
    assert "OMEMO_AUTO_ENCRYPT_ADMIN_ROOM must be True or False" in errors
    assert "OMEMO_PLAINTEXT_FALLBACK must be True or False" in errors


def test_validate_config_requires_rtbl_publish_details_when_enabled(monkeypatch):
    set_valid_config(monkeypatch)
    monkeypatch.setattr(config, "RTBL_PUBLISH_ENABLED", True, raising=False)
    monkeypatch.setattr(config, "RTBL_PUBLISH_SERVICE", "pubsub", raising=False)
    monkeypatch.setattr(config, "RTBL_PUBLISH_JID_NODE", "", raising=False)
    monkeypatch.setattr(config, "RTBL_PUBLISH_DOMAIN_NODE", "", raising=False)
    bot = ConfigValidationBot()

    errors, _warnings = bot._validate_config()

    assert "RTBL_PUBLISH_SERVICE must be a valid JID like pubsub.domain.tld" in errors
    assert "RTBL_PUBLISH_JID_NODE must not be empty when RTBL_PUBLISH_ENABLED=True" in errors
    assert "RTBL_PUBLISH_DOMAIN_NODE must not be empty when RTBL_PUBLISH_ENABLED=True" in errors


def test_resource_prefers_resource_over_legacy_ressource(monkeypatch):
    monkeypatch.setattr(config, "RESOURCE", "new", raising=False)
    monkeypatch.setattr(config, "RESSOURCE", "old", raising=False)
    assert get_config_resource() == "new"

    monkeypatch.delattr(config, "RESOURCE", raising=False)
    assert get_config_resource() == "old"


def test_format_config_import_error_for_name_error_includes_hint():
    try:
        raise NameError("name 'false' is not defined")
    except NameError as exc:
        rendered = format_config_import_error(exc)

    assert "NameError" in rendered
    assert "true/false" in rendered


def test_validate_config_warns_but_does_not_error_when_omemo_dependency_missing(monkeypatch):
    import importlib.util

    set_valid_config(monkeypatch)
    monkeypatch.setattr(config, "OMEMO_ENABLED", True, raising=False)
    monkeypatch.setattr(config, "OMEMO_STORAGE_FILE", "data/omemo.json", raising=False)

    real_find_spec = importlib.util.find_spec

    def fake_find_spec(name):
        if name in {"slixmpp_omemo", "omemo"}:
            return None
        return real_find_spec(name)

    monkeypatch.setattr(importlib.util, "find_spec", fake_find_spec)
    bot = ConfigValidationBot()

    errors, warnings = bot._validate_config()

    assert "OMEMO_ENABLED=True requires optional dependency slixmpp-omemo>=2,<3" not in errors
    assert any("OMEMO_ENABLED=True but optional OMEMO dependencies are not installed" in warning for warning in warnings)
