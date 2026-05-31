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

    storage = _section(body, "💾 Storage")
    identity = _section(body, "🪪 Bot Identity")
    admin_control = _section(body, "🛡️ Admin / Control Room")
    connection = _section(body, "🌐 Connection")
    profile = _section(body, "🖼️ Profile / vCard")
    command_access = _section(body, "🧭 Command Access")
    performance = _section(body, "⚡ Performance")

    assert "DB_FILE" in storage
    assert "JID" in identity
    assert "RESOURCE" in identity
    assert "PASSWORD = ****" in identity
    assert "NICK" in identity
    assert "ADMIN_ROOM" in admin_control
    assert "CONNECT_HOST" in connection
    assert "CONNECT_PORT" in connection
    assert "AVATAR_PATH" in profile
    assert "PUBLIC_COMMAND_RATE_LIMIT_WINDOW" in command_access
    assert "PUBLIC_COMMAND_RATE_LIMIT_MAX" in command_access
    assert "MUC_WRITE_SEMAPHORE" in performance

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
