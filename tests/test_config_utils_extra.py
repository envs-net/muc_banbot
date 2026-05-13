import logging

from banbot.config_utils import ConfigMixin, format_config_import_error


class ConfigBot(ConfigMixin):
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


def test_apply_log_level_falls_back_to_info_for_invalid_level():
    bot = ConfigBot()

    effective = bot.apply_log_level("definitely-not-a-level")

    assert effective == "INFO"
    assert logging.getLogger("banbot").level == logging.INFO


def test_format_startup_only_changes_includes_omemo_fields():
    bot = ConfigBot()
    before = {"OMEMO_ENABLED": False, "OMEMO_STORAGE_FILE": "old.json"}
    after = {"OMEMO_ENABLED": True, "OMEMO_STORAGE_FILE": "new.json"}

    changes = bot._format_startup_only_changes(before, after)

    assert "- OMEMO_ENABLED: False → True" in changes
    assert "- OMEMO_STORAGE_FILE: 'old.json' → 'new.json'" in changes


def test_format_config_changes_reports_runtime_changes():
    bot = ConfigBot()
    before = {"COMMAND_PREFIX": "!", "RTBL_REFRESH_INTERVAL": 3600}
    after = {"COMMAND_PREFIX": ".", "RTBL_REFRESH_INTERVAL": 0}

    changes = bot._format_config_changes(before, after)

    assert "- COMMAND_PREFIX: '!' → '.'" in changes
    assert "- RTBL_REFRESH_INTERVAL: 3600 → 0" in changes


def test_format_config_import_error_includes_syntax_location():
    exc = SyntaxError("bad syntax", ("config.py", 12, 5, "JID =\n"))

    rendered = format_config_import_error(exc)

    assert "config.py:12" in rendered
    assert "JID =" in rendered
    assert "^" in rendered
