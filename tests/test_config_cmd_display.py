"""Config command display-format coverage."""

from __future__ import annotations

import pytest

from banbot.config_cmd import ConfigCommandMixin


class ConfigDisplayBot(ConfigCommandMixin):
    def __init__(self):
        self.sent = []
        self.command_prefix = "!"
        self.omemo_auto_encrypt_admin_room = True
        self.omemo_plaintext_fallback = False
        self.omemo_reset_on_identity_change = True
        self.rtbl_publish_enabled = False

    async def bot_send_message(self, **kwargs):
        self.sent.append(kwargs)


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

    # Removed legacy summary lines should not make the output noisy again.
    assert "🪪 JID:" not in body
    assert "🔐 OMEMO Enabled:" not in body
    assert "🛡️ RTBL Enabled:" not in body


@pytest.mark.asyncio
async def test_config_show_shortens_long_lists_and_keeps_runtime_summary(monkeypatch):
    import config

    monkeypatch.setattr(
        config,
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
