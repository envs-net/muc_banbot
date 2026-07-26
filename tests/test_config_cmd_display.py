"""Config command display-format coverage."""

from __future__ import annotations

import config as test_config
import pytest

from banbot.commands.config_display import ConfigCommandMixin


class ConfigDisplayBot(ConfigCommandMixin):
    def __init__(self):
        self.sent = []
        self.audit_events = []
        self.command_prefix = "!"
        self.omemo_auto_encrypt_admin_room = True
        self.omemo_plaintext_fallback = False
        self.omemo_reset_on_identity_change = True
        self.rtbl_publish_enabled = False

    async def bot_send_message(self, **kwargs):
        self.sent.append(kwargs)

    async def audit_event(self, event_type, **kwargs):
        self.audit_events.append((event_type, kwargs))


def _section(body: str, title: str) -> str:
    start = body.index(title)
    rest = body[start:]
    marker = "\n\n"
    end = rest.find(marker, len(title))
    return rest if end == -1 else rest[:end]


@pytest.mark.asyncio
async def test_config_show_groups_keys_and_hides_secrets():
    bot = ConfigDisplayBot()

    await bot._cmd_config_show("admin@conference.example.org")
    body = bot.sent[-1]["mbody"]

    assert body.startswith("📋 Current Bot Configuration")
    assert "🔒 = restart-only/protected, ✏️ = runtime-writable" in body
    assert "Password/secret values are hidden." in body

    identity = _section(body, "🪪 Bot Identity / Control")
    storage = _section(body, "💾 Database / Backups")
    exports = _section(body, "📦 Managed CSV Exports")
    connection = _section(body, "🌐 Connection")
    profile = _section(body, "🖼️ vCard Settings")
    bot_settings = _section(body, "⚙️ Bot Settings")
    performance = _section(body, "⚡ Performance Tuning")

    assert "JID" in identity
    assert "RESOURCE" in identity
    assert "PASSWORD = ****" in identity
    assert "ADMIN_ROOM" in identity
    assert "NICK" in identity
    assert "DB_FILE" in storage
    assert "DB_BACKUP_KEEP" in storage
    assert "EXPORT_DIR" in exports
    assert "EXPORT_KEEP" in exports
    assert "CONNECT_HOST" in connection
    assert "CONNECT_PORT" in connection
    assert "AVATAR_PATH" in profile
    assert "PUBLIC_COMMAND_RATE_LIMIT_WINDOW" in bot_settings
    assert "PUBLIC_COMMAND_RATE_LIMIT_MAX" in bot_settings
    assert "LIST_PAGE_SIZE" in bot_settings
    assert "MUC_WRITE_SEMAPHORE" in performance
    assert "SYNC_BATCH_SIZE" in performance
    assert "MUC_JOIN_TIMEOUT_SECONDS" in bot_settings
    assert "MUC_JOIN_RETRIES" in bot_settings

    # Removed legacy summary lines should not make the output noisy again.
    assert "🪪 JID:" not in body
    assert "🔐 OMEMO Enabled:" not in body
    assert "🛡️ RTBL Enabled:" not in body


@pytest.mark.asyncio
async def test_config_show_shortens_long_lists_and_keeps_runtime_summary(monkeypatch):
    monkeypatch.setattr(
        test_config,
        "REDACTION_AUTO_REASONS",
        ["one", "two", "three", "four", "five", "six", "seven"],
        raising=False,
    )
    bot = ConfigDisplayBot()

    await bot._cmd_config_show("admin@conference.example.org")
    body = bot.sent[-1]["mbody"]

    assert "REDACTION_AUTO_REASONS = ['one', 'two', 'three', 'four', ...] (7 items)" in body
    assert "🔐 OMEMO Runtime:" in body
    assert "Auto-encrypt admin room: True" in body
    assert "Plaintext fallback: False" in body
    assert "Commands:" in body
    assert "!config set <KEY> <value>" in body


@pytest.mark.asyncio
async def test_config_show_follows_config_sample_order():
    bot = ConfigDisplayBot()

    await bot._cmd_config_show("admin@conference.example.org")
    body = bot.sent[-1]["mbody"]

    assert body.index("🪪 Bot Identity / Control") < body.index("🌐 Connection")
    assert body.index("🌐 Connection") < body.index("💾 Database / Backups")
    assert body.index("💾 Database / Backups") < body.index("📦 Managed CSV Exports")


@pytest.mark.asyncio
async def test_config_show_default_all_and_paginated_mode():
    bot = ConfigDisplayBot()
    bot.config_output_mode = "all"
    bot.list_page_size = 8

    await bot._cmd_config_show("admin@conference.example.org")
    body = bot.sent[-1]["mbody"]
    assert "page 1/" not in body
    assert "⚡ Performance Tuning" in body

    bot.config_output_mode = "paginate"
    await bot._cmd_config_show("admin@conference.example.org")
    body = bot.sent[-1]["mbody"]
    assert "Current Bot Configuration" in body
    assert "page 1/" in body
    assert "Use !config all for the full output." in body

    await bot._cmd_config_show("admin@conference.example.org", ["all"])
    body = bot.sent[-1]["mbody"]
    assert "page 1/" not in body
    assert "⚡ Performance Tuning" in body


@pytest.mark.asyncio
async def test_config_show_accepts_page_and_last_args():
    bot = ConfigDisplayBot()
    bot.config_output_mode = "paginate"
    bot.list_page_size = 5

    await bot._cmd_config_show("admin@conference.example.org", ["last"])
    body = bot.sent[-1]["mbody"]
    assert "page " in body
    assert "Commands:" in body

    await bot._cmd_config_show("admin@conference.example.org", ["2"])
    body = bot.sent[-1]["mbody"]
    assert "page 2/" in body
    assert "RESOURCE" in body


@pytest.mark.asyncio
async def test_config_search_finds_key_and_keeps_secret_values_hidden():
    bot = ConfigDisplayBot()

    await bot._cmd_config_search("admin@conference.example.org", ["CONNECT_DIRECT_TLS"])
    body = bot.sent[-1]["mbody"]

    assert "Config search for 'CONNECT_DIRECT_TLS': 1 match(es)" in body
    assert "CONNECT_DIRECT_TLS =" in body

    await bot._cmd_config_search("admin@conference.example.org", ["PASSWORD"])
    body = bot.sent[-1]["mbody"]

    assert "PASSWORD = ****" in body
    password = getattr(test_config, "PASSWORD", None)
    if password is not None:
        assert str(password) not in body


@pytest.mark.asyncio
async def test_config_search_reports_usage_and_no_matches():
    bot = ConfigDisplayBot()

    await bot._cmd_config_search("admin@conference.example.org", [])
    assert bot.sent[-1]["mbody"] == "❌ Usage: !config search/find <query>"

    await bot._cmd_config_search("admin@conference.example.org", ["definitely-no-such-config-key"])
    assert bot.sent[-1]["mbody"] == "🔎 Config search for 'definitely-no-such-config-key': no matches."


@pytest.mark.asyncio
async def test_config_diff_shows_only_supported_non_secret_changes(monkeypatch):
    bot = ConfigDisplayBot()
    monkeypatch.setattr(test_config, "LOG_LEVEL", "DEBUG", raising=False)
    monkeypatch.setattr(test_config, "PASSWORD", "super-secret-test-value", raising=False)
    monkeypatch.setattr(test_config, "LOCAL_ONLY_TEST_OPTION", "local", raising=False)

    await bot._cmd_config_diff("admin@conference.example.org")
    body = bot.sent[-1]["mbody"]

    assert "🧩 Config Diff" in body
    assert "• LOG_LEVEL" in body
    assert "current: 'DEBUG'" in body
    assert "default: 'INFO'" in body
    assert "PASSWORD" not in body
    assert "super-secret-test-value" not in body
    assert "LOCAL_ONLY_TEST_OPTION" not in body


@pytest.mark.asyncio
async def test_config_diff_reports_no_changes_when_values_match_defaults(monkeypatch):
    bot = ConfigDisplayBot()
    defaults = bot._config_default_values_from_sample()
    for key, value in defaults.items():
        monkeypatch.setattr(test_config, key, value, raising=False)

    await bot._cmd_config_diff("admin@conference.example.org")

    assert bot.sent[-1]["mbody"] == "🧩 Config Diff: no differences from config_sample.py defaults."


@pytest.mark.asyncio
async def test_config_diff_supports_paging(monkeypatch):
    bot = ConfigDisplayBot()
    bot.config_output_mode = "paginate"
    bot.list_page_size = 4
    monkeypatch.setattr(test_config, "LOG_LEVEL", "DEBUG", raising=False)
    monkeypatch.setattr(test_config, "PUBLIC_COMMAND_RATE_LIMIT_WINDOW", 20, raising=False)

    await bot._cmd_config_diff("admin@conference.example.org", ["last"])
    body = bot.sent[-1]["mbody"]

    assert "🧩 Config Diff" in body
    assert "Page " in body
    assert "Use !config diff all for the full output." in body


@pytest.mark.asyncio
async def test_config_change_audit_includes_changed_key_and_message():
    bot = ConfigDisplayBot()

    await bot._audit_config_change(
        "admin@example.test",
        "set",
        "LOG_LEVEL",
        True,
        "✅ LOG_LEVEL updated: 'INFO' → 'DEBUG'",
    )

    assert bot.audit_events == [
        (
            "config_changed",
            {
                "actor": "admin@example.test",
                "target_type": "config",
                "target": "LOG_LEVEL",
                "comment": "set: ✅ LOG_LEVEL updated: 'INFO' → 'DEBUG'",
                "details": {
                    "action": "set",
                    "key": "LOG_LEVEL",
                    "ok": True,
                    "message": "✅ LOG_LEVEL updated: 'INFO' → 'DEBUG'",
                },
            },
        )
    ]


@pytest.mark.asyncio
async def test_config_change_audit_emits_failed_event_when_update_fails():
    bot = ConfigDisplayBot()

    await bot._audit_config_change(
        "admin@example.test",
        "set",
        "LOG_LEVEL",
        False,
        "❌ LOG_LEVEL rejected: invalid value 'TRACE2'",
    )

    assert bot.audit_events == [
        (
            "config_change_failed",
            {
                "actor": "admin@example.test",
                "target_type": "config",
                "target": "LOG_LEVEL",
                "comment": "set: ❌ LOG_LEVEL rejected: invalid value 'TRACE2'",
                "details": {
                    "action": "set",
                    "key": "LOG_LEVEL",
                    "ok": False,
                    "message": "❌ LOG_LEVEL rejected: invalid value 'TRACE2'",
                },
            },
        )
    ]
