"""Additional config validation and reload helper coverage."""

from __future__ import annotations

import logging

import pytest

import config
from banbot.config_utils import ConfigMixin, format_config_import_error, get_config_resource


class ConfigValidationBot(ConfigMixin):
    def __init__(self):
        self.command_prefix = "!"
        self.announce_startup = True
        self.announce_sync_details = True
        self.show_ban_in_muc = False
        self.allow_user_cmds = True
        self.health_check_interval = 300
        self.unban_check_interval = 60
        self.max_tempban_days = 30
        self.public_command_rate_limit_window = 30
        self.public_command_rate_limit_max = 3
        self.muc_write_limit = 5
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
        "LOG_LEVEL": "INFO",
        "COMMAND_PREFIX": "!",
        "AUDIT_LOG_RETENTION_DAYS": 365,
        "HEALTH_CHECK_INTERVAL": 300,
        "UNBAN_CHECK_INTERVAL": 60,
        "MAX_TEMPBAN_DAYS": 30,
        "PUBLIC_COMMAND_RATE_LIMIT_WINDOW": 30,
        "PUBLIC_COMMAND_RATE_LIMIT_MAX": 3,
        "MUC_WRITE_SEMAPHORE": 5,
        "VERSION_CHECK_INTERVAL": 3600,
        "RTBL_REFRESH_INTERVAL": 3600,
    }
    for key, value in values.items():
        monkeypatch.setattr(config, key, value, raising=False)


def test_validate_config_accepts_valid_values(monkeypatch):
    set_valid_config(monkeypatch)
    bot = ConfigValidationBot()

    errors, warnings = bot._validate_config()

    assert errors == []
    assert warnings == []


def test_validate_config_reports_multiple_errors_and_placeholder_warning(monkeypatch):
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
    assert "PASSWORD still looks like a placeholder" in warnings


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
    assert bot.rtbl_refresh_interval == 0
    assert logging.getLogger("banbot").level == logging.ERROR


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
