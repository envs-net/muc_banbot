"""Verify BanBot's room routing consumes shared message snapshots."""

import pytest

from banbot.commands import entrypoint
from banbot.commands.entrypoint import CommandEntryPointMixin


class _Sender:
    bare = "room@conference.example.org"
    resource = "Alice"

    def __str__(self) -> str:
        return "room@conference.example.org/Alice"


class _Bot(CommandEntryPointMixin):
    command_prefix = "!"

    def __init__(self) -> None:
        self.tokens: list[bool] = []
        self.reset_count = 0
        self.calls: list[tuple[str, str, str, str, tuple[str, ...]]] = []

    def _set_reply_encryption_context(self, encrypted: bool) -> str:
        self.tokens.append(encrypted)
        return "token"

    def _reset_reply_encryption_context(self, token: object) -> None:
        assert token == "token"
        self.reset_count += 1

    async def _handle_user_command(self, _msg, room, nick, cmd, args) -> bool:
        self.calls.append(("user", room, nick, cmd, tuple(args)))
        return True


@pytest.mark.asyncio
async def test_groupchat_context_keeps_command_dispatch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(entrypoint, "bot_nick", lambda: "BanBot")
    bot = _Bot()
    msg = {"from": _Sender(), "type": "groupchat", "mucnick": "Alice", "body": "!status"}
    await bot.on_message(msg)
    assert bot.calls == [("user", _Sender.bare, "Alice", "status", ())]
    assert bot.tokens == [False]
    assert bot.reset_count == 1


@pytest.mark.asyncio
async def test_encrypted_context_uses_decrypted_body(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(entrypoint, "bot_nick", lambda: "BanBot")
    bot = _Bot()
    clear = {"from": _Sender(), "type": "groupchat", "mucnick": "Alice", "body": "!status full"}

    async def decrypt(_msg):
        return clear, True

    bot._decrypt_incoming_omemo_message = decrypt  # type: ignore[attr-defined]
    await bot.on_message({**clear, "body": "encrypted-placeholder"})
    assert bot.calls == [("user", _Sender.bare, "Alice", "status", ("full",))]
    assert bot.tokens == [True]
    assert bot.reset_count == 1

@pytest.mark.asyncio
async def test_chat_stanza_with_mucnick_does_not_dispatch_as_room_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A MUC nick alone must not turn a chat stanza into a room command."""
    monkeypatch.setattr(entrypoint, "bot_nick", lambda: "BanBot")
    bot = _Bot()
    msg = {"from": _Sender(), "type": "chat", "mucnick": "Alice", "body": "!status"}
    await bot.on_message(msg)
    assert bot.calls == []
    assert bot.tokens == []
    assert bot.reset_count == 0
