from __future__ import annotations

import pytest

from banbot.commands.moderation import CommandModerationMixin


class EditBot(CommandModerationMixin):
    command_prefix = "!"

    def __init__(self):
        self.sent = []
        self.calls = []
        self.row = (1, "jid", "user@example.org", "user@example.org", "User", 0, "old-admin@example.org", "old reason", 100, 100)

    async def bot_send_message(self, **kwargs):
        self.sent.append(kwargs)

    def _actor_jid_from_room_nick(self, room, nick):
        return "admin@example.org"

    async def _find_ban_record(self, target):
        return self.row

    async def ban_all(self, target, until, issuer, comment=None, *, notify_policy=True, auto_redact=True):
        self.calls.append((target, until, issuer, comment, notify_policy))

    async def cmd_baninfo(self, identifier, room):
        self.calls.append(("baninfo", identifier, room))

    async def cmd_history(self, identifier, room, args):
        self.calls.append(("history", identifier, room, args))


@pytest.mark.asyncio
async def test_banedit_reason_uses_current_ban_type_and_suppresses_policy():
    bot = EditBot()
    await bot._dispatch_banedit_command("admin@conference.example.org", "Admin", ["user@example.org", "reason", "new", "reason"], "banedit")
    assert bot.calls == [("user@example.org", None, "admin@example.org", "new reason", False)]


@pytest.mark.asyncio
async def test_banedit_temp_converts_permanent_ban(monkeypatch):
    bot = EditBot()
    monkeypatch.setattr("banbot.commands.moderation.time.time", lambda: 1000)
    await bot._dispatch_banedit_command("admin@conference.example.org", "Admin", ["user@example.org", "temp", "10m"], "banedit")
    assert bot.calls == [("user@example.org", 1600, "admin@example.org", "old reason", False)]


@pytest.mark.asyncio
async def test_baninfo_and_history_dispatch():
    bot = EditBot()
    await bot._dispatch_baninfo_command("admin@conference.example.org", "Admin", ["user@example.org"], "baninfo")
    await bot._dispatch_history_command("admin@conference.example.org", "Admin", ["user@example.org", "2"], "history")
    assert bot.calls == [
        ("baninfo", "user@example.org", "admin@conference.example.org"),
        ("history", "user@example.org", "admin@conference.example.org", ["2"]),
    ]
