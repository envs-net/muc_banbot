import importlib
import pytest

from banbot.commands import CommandMixin
from banbot.messaging import MessagingMixin


class CommandBot(CommandMixin, MessagingMixin):
    def __init__(self):
        self.command_prefix = "!"
        self.protected_rooms = {"room@conference.example.test"}
        self.allow_user_cmds = True
        self.public_command_rate_limit_hits = {}
        self.public_command_rate_limit_window = 30
        self.public_command_rate_limit_max = 3
        self.sent = []
        self.user_handled = False
        self.admin_handled = False
        self.unknown_seen = False
        self.decrypt_returns_encrypted = False
        self.decrypt_returns_none = False

    async def bot_send_message(self, **kwargs):
        kwargs["context_encrypted"] = self._get_reply_encryption_context()
        self.sent.append(kwargs)

    async def _decrypt_incoming_omemo_message(self, msg):
        if self.decrypt_returns_none:
            return None, True
        return msg, self.decrypt_returns_encrypted

    async def _handle_user_command(self, msg, room, nick, cmd, args):
        if cmd == "ping":
            self.user_handled = True
            await self.bot_send_message(mto=room, mbody="pong", mtype="groupchat")
            return True
        return False

    async def _handle_admin_command(self, msg, room, nick, cmd, args):
        if cmd == "admin":
            self.admin_handled = True
            return True
        return False

    async def _handle_unknown_command(self, msg, room, cmd):
        self.unknown_seen = True

    def is_authorized(self, msg):
        return True


@pytest.mark.asyncio
async def test_on_message_ignores_non_commands(fake_msg_factory, monkeypatch):
    commands = importlib.import_module("banbot.commands")

    monkeypatch.setattr(commands, "NICK", "adminbot")
    bot = CommandBot()
    await bot.on_message(fake_msg_factory(body="hello"))
    assert not bot.user_handled
    assert not bot.unknown_seen


@pytest.mark.asyncio
async def test_on_message_routes_user_command_and_preserves_plain_context(fake_msg_factory, monkeypatch):
    commands = importlib.import_module("banbot.commands")

    monkeypatch.setattr(commands, "NICK", "adminbot")
    bot = CommandBot()
    await bot.on_message(fake_msg_factory(body="!ping"))
    assert bot.user_handled
    assert bot.sent[0]["mbody"] == "pong"
    assert bot.sent[0]["context_encrypted"] is False


@pytest.mark.asyncio
async def test_on_message_sets_encrypted_context_for_decrypted_command(fake_msg_factory, monkeypatch):
    commands = importlib.import_module("banbot.commands")

    monkeypatch.setattr(commands, "NICK", "adminbot")
    bot = CommandBot()
    bot.decrypt_returns_encrypted = True
    await bot.on_message(fake_msg_factory(body="!ping"))
    assert bot.sent[0]["context_encrypted"] is True


@pytest.mark.asyncio
async def test_on_message_stops_when_omemo_decrypt_fails(fake_msg_factory, monkeypatch):
    commands = importlib.import_module("banbot.commands")

    monkeypatch.setattr(commands, "NICK", "adminbot")
    bot = CommandBot()
    bot.decrypt_returns_none = True
    await bot.on_message(fake_msg_factory(body="!ping"))
    assert not bot.user_handled
    assert not bot.sent


def test_public_command_rate_limit(monkeypatch):
    commands = importlib.import_module("banbot.commands")

    monkeypatch.setattr(commands, "ADMIN_ROOM", "admin@conference.example.test")
    bot = CommandBot()
    ok, retry = bot.check_public_command_rate_limit("room@conference.example.test", "alice", "help")
    assert ok and retry == 0
    bot.check_public_command_rate_limit("room@conference.example.test", "alice", "help")
    bot.check_public_command_rate_limit("room@conference.example.test", "alice", "help")
    ok, retry = bot.check_public_command_rate_limit("room@conference.example.test", "alice", "help")
    assert not ok
    assert retry >= 1
