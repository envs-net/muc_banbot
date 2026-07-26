import logging

from banbot.config import ConfigMixin, format_config_import_error


class ConfigBot(ConfigMixin):
    def __init__(self):
        self.command_prefix = "!"
        self.announce_startup = True
        self.announce_sync_details = True
        self.show_ban_in_muc = False
        self.allow_user_cmds = True
        self.allow_admin_commands_in_dms = True
        self.room_invites_enabled = False
        self.room_invite_max_age_days = 30
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
        self.rtbl_refresh_interval = 3600
        self.redaction_enabled = False
        self.redaction_index_retention_days = 30
        self.redaction_auto_reasons = []
        self.version_check_enabled = False
        self.version_check_interval = 3600
        self.version_check_url = None
        self.log_level = "INFO"
        self.config_output_mode = "all"
        self.help_output_mode = "all"


def test_apply_log_level_falls_back_to_info_for_invalid_level():
    bot = ConfigBot()

    effective = bot.apply_log_level("definitely-not-a-level")

    assert effective == "INFO"
    assert logging.getLogger("banbot").level == logging.INFO


def test_format_startup_only_changes_includes_rtbl_and_omemo_fields():
    bot = ConfigBot()
    before = {
        "RTBL_ENABLED": False,
        "RTBL_PUBLISH_ENABLED": False,
        "RTBL_PUBLISH_SERVICE": "pubsub.old.example.org",
        "RTBL_PUBLISH_JID_NODE": "old_hashes",
        "RTBL_PUBLISH_DOMAIN_NODE": "old_domains",
        "OMEMO_ENABLED": False,
        "OMEMO_STORAGE_FILE": "old.json",
        "OMEMO_AUTO_ENCRYPT_ADMIN_ROOM": False,
        "OMEMO_PLAINTEXT_FALLBACK": False,
    }
    after = {
        "RTBL_ENABLED": True,
        "RTBL_PUBLISH_ENABLED": True,
        "RTBL_PUBLISH_SERVICE": "pubsub.new.example.org",
        "RTBL_PUBLISH_JID_NODE": "new_hashes",
        "RTBL_PUBLISH_DOMAIN_NODE": "new_domains",
        "OMEMO_ENABLED": True,
        "OMEMO_STORAGE_FILE": "new.json",
        "OMEMO_AUTO_ENCRYPT_ADMIN_ROOM": True,
        "OMEMO_PLAINTEXT_FALLBACK": True,
    }

    changes = bot._format_startup_only_changes(before, after)

    assert "- RTBL_ENABLED: False → True" in changes
    assert "- RTBL_PUBLISH_ENABLED: False → True" in changes
    assert "- RTBL_PUBLISH_SERVICE: 'pubsub.old.example.org' → 'pubsub.new.example.org'" in changes
    assert "- RTBL_PUBLISH_JID_NODE: 'old_hashes' → 'new_hashes'" in changes
    assert "- RTBL_PUBLISH_DOMAIN_NODE: 'old_domains' → 'new_domains'" in changes
    assert "- OMEMO_ENABLED: False → True" in changes
    assert "- OMEMO_STORAGE_FILE: 'old.json' → 'new.json'" in changes
    assert "- OMEMO_AUTO_ENCRYPT_ADMIN_ROOM: False → True" in changes
    assert "- OMEMO_PLAINTEXT_FALLBACK: False → True" in changes




def test_startup_config_snapshot_includes_rtbl_and_omemo(monkeypatch):
    import config

    monkeypatch.setattr(config, "RTBL_ENABLED", True, raising=False)
    monkeypatch.setattr(config, "RTBL_PUBLISH_ENABLED", True, raising=False)
    monkeypatch.setattr(config, "RTBL_PUBLISH_SERVICE", "pubsub.example.org", raising=False)
    monkeypatch.setattr(config, "RTBL_PUBLISH_JID_NODE", "hashes", raising=False)
    monkeypatch.setattr(config, "RTBL_PUBLISH_DOMAIN_NODE", "domains", raising=False)
    monkeypatch.setattr(config, "OMEMO_ENABLED", True, raising=False)
    monkeypatch.setattr(config, "OMEMO_STORAGE_FILE", "data/omemo-test.json", raising=False)
    monkeypatch.setattr(config, "OMEMO_AUTO_ENCRYPT_ADMIN_ROOM", True, raising=False)
    monkeypatch.setattr(config, "OMEMO_PLAINTEXT_FALLBACK", False, raising=False)
    bot = ConfigBot()

    snapshot = bot._startup_config_snapshot()

    assert snapshot["RTBL_ENABLED"] is True
    assert snapshot["RTBL_PUBLISH_ENABLED"] is True
    assert snapshot["RTBL_PUBLISH_SERVICE"] == "pubsub.example.org"
    assert snapshot["RTBL_PUBLISH_JID_NODE"] == "hashes"
    assert snapshot["RTBL_PUBLISH_DOMAIN_NODE"] == "domains"
    assert snapshot["OMEMO_ENABLED"] is True
    assert snapshot["OMEMO_STORAGE_FILE"] == "data/omemo-test.json"
    assert snapshot["OMEMO_AUTO_ENCRYPT_ADMIN_ROOM"] is True
    assert snapshot["OMEMO_PLAINTEXT_FALLBACK"] is False



def test_runtime_config_snapshot_includes_sync_batch_list_page_size_and_output_modes():
    bot = ConfigBot()
    bot.sync_batch_size = 7
    bot.muc_join_timeout_seconds = 45
    bot.muc_join_retries = 4
    bot.list_page_size = 13
    bot.config_output_mode = "paginate"
    bot.help_output_mode = "paginate"

    snapshot = bot._runtime_config_snapshot()

    assert snapshot["SYNC_BATCH_SIZE"] == 7
    assert snapshot["MUC_JOIN_TIMEOUT_SECONDS"] == 45
    assert snapshot["MUC_JOIN_RETRIES"] == 4
    assert snapshot["LIST_PAGE_SIZE"] == 13
    assert snapshot["CONFIG_OUTPUT_MODE"] == "paginate"
    assert snapshot["HELP_OUTPUT_MODE"] == "paginate"


def test_runtime_config_snapshot_includes_rtbl_refresh_interval():
    bot = ConfigBot()
    bot.rtbl_refresh_interval = 3600

    snapshot = bot._runtime_config_snapshot()

    assert snapshot["RTBL_REFRESH_INTERVAL"] == 3600


def test_format_config_changes_reports_runtime_changes():
    bot = ConfigBot()
    before = {"COMMAND_PREFIX": "!", "RTBL_REFRESH_INTERVAL": 3600}
    after = {"COMMAND_PREFIX": ".", "RTBL_REFRESH_INTERVAL": 0}

    changes = bot._format_config_changes(before, after)

    assert "- COMMAND_PREFIX: '!' → '.'" in changes
    assert "- RTBL_REFRESH_INTERVAL: 3600 → 0" in changes


def test_runtime_config_snapshot_includes_admin_dm_commands_enabled():
    bot = ConfigBot()
    bot.allow_admin_commands_in_dms = False

    snapshot = bot._runtime_config_snapshot()

    assert snapshot["ALLOW_ADMIN_COMMANDS_IN_DMS"] is False


def test_runtime_config_snapshot_includes_room_invites_enabled():
    bot = ConfigBot()
    bot.room_invites_enabled = True
    bot.room_invite_max_age_days = 7

    snapshot = bot._runtime_config_snapshot()

    assert snapshot["ROOM_INVITES_ENABLED"] is True
    assert snapshot["ROOM_INVITE_MAX_AGE_DAYS"] == 7


def test_runtime_config_snapshot_includes_redaction_settings():
    bot = ConfigBot()
    bot.redaction_enabled = True
    bot.redaction_index_retention_days = 0
    bot.redaction_auto_reasons = ["spam", "abuse"]

    snapshot = bot._runtime_config_snapshot()

    assert snapshot["REDACTION_ENABLED"] is True
    assert snapshot["REDACTION_INDEX_RETENTION_DAYS"] == 0
    assert snapshot["REDACTION_AUTO_REASONS"] == ("spam", "abuse")


def test_format_config_import_error_for_missing_config():
    exc = ModuleNotFoundError("No module named 'config'", name="config")

    message = format_config_import_error(exc)

    assert "config.py is missing" in message
    assert "cp config_sample.py config.py" in message
    assert "Then edit config.py and start the bot again." in message


def test_format_config_import_error_includes_syntax_location():
    exc = SyntaxError("bad syntax", ("config.py", 12, 5, "JID =\n"))

    rendered = format_config_import_error(exc)

    assert "config.py:12" in rendered
    assert "JID =" in rendered
    assert "^" in rendered


def test_output_modes_are_runtime_writable_config_keys():
    assert "CONFIG_OUTPUT_MODE" in ConfigMixin.CONFIG_KEYS
    assert "HELP_OUTPUT_MODE" in ConfigMixin.CONFIG_KEYS
    assert "CONFIG_OUTPUT_MODE" not in ConfigMixin.CONFIG_NEVER_WRITABLE_KEYS
    assert "HELP_OUTPUT_MODE" not in ConfigMixin.CONFIG_NEVER_WRITABLE_KEYS
