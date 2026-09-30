"""BanBot's real adapters consume the same contract vectors as envsbot."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from envs_xmpp_core.runtime.rooms import RoomLifecycleRegistry
from envs_xmpp_ops.contract_cases import CONFIG_CASES, ENCRYPTION_CASES, INCOMING_CASES, ROOM_CASES

from banbot.commands import entrypoint
from banbot.commands.entrypoint import CommandEntryPointMixin
from banbot.config.runtime import ConfigRuntimeMixin
from banbot.messaging import MessagingMixin
from banbot.occupants import BotOccupantMixin
from banbot.omemo.core import OmemoCoreMixin
from banbot.status import StatusMixin


class _Sender:
    def __init__(self, jid: str) -> None:
        self.bare, _, self.resource = jid.partition("/")
        self.jid = jid

    def __str__(self) -> str:
        return self.jid


class _IncomingBot(CommandEntryPointMixin):
    command_prefix = "!"

    def __init__(self) -> None:
        self.handled: list[tuple[str, str]] = []

    async def _handle_user_command(self, _msg, _room, _nick, command, _args) -> bool:
        self.handled.append(("user", command))
        return True

    def _set_reply_encryption_context(self, _encrypted: bool) -> str:
        return "token"

    def _reset_reply_encryption_context(self, token: str) -> None:
        assert token == "token"


@pytest.mark.asyncio
@pytest.mark.parametrize("case", INCOMING_CASES, ids=lambda case: case.name)
async def test_only_real_groupchat_is_routed_to_muc_commands(monkeypatch: pytest.MonkeyPatch, case) -> None:
    monkeypatch.setattr(entrypoint, "bot_nick", lambda: "BanBot")
    bot = _IncomingBot()
    msg = {"from": _Sender(case.sender), "type": case.message_type, "mucnick": "Alice", "body": "!status"}
    await bot.on_message(msg)
    assert bot.handled == ([("user", "status")] if case.public_command else [])


@pytest.mark.asyncio
async def test_omemo_decrypt_failure_cannot_dispatch_ciphertext(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(entrypoint, "bot_nick", lambda: "BanBot")
    bot = _IncomingBot()

    async def decrypt(_msg) -> tuple[None, bool]:
        return None, True

    bot._decrypt_incoming_omemo_message = decrypt  # type: ignore[attr-defined]
    await bot.on_message({"from": _Sender("room@conference.example.test/Alice"),
                          "type": "groupchat", "mucnick": "Alice", "body": "cipher"})
    assert not bot.handled


@pytest.mark.parametrize("case", ROOM_CASES, ids=lambda case: case.name)
def test_status_rooms_rely_on_verified_occupant(
    monkeypatch: pytest.MonkeyPatch, case
) -> None:
    room = "room@conference.example.test"
    registry = RoomLifecycleRegistry()
    if case.lifecycle == "joining":
        registry.begin_join(room)
    elif case.lifecycle == "failed":
        registry.mark_failed(room)
    elif case.lifecycle == "deferred":
        registry.mark_deferred(room)
    elif case.lifecycle == "leaving":
        registry.begin_leave(room)
    elif case.lifecycle == "joined":
        registry.confirm_self_presence(room, "Bot")
    occupant = ("Bot", {"affiliation": "admin", "role": "moderator"}) if case.presence_verified else (None, None)
    monkeypatch.setattr(BotOccupantMixin, "_bot_occupant_entry", lambda _host, _room: occupant)
    bot = SimpleNamespace(room_lifecycle=registry)
    view = StatusMixin._status_room_views(bot, [room])[0]
    assert view.joined is case.presence_verified
    assert view.state == case.expected_state
    assert view.needs_attention is case.needs_attention


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ENCRYPTION_CASES, ids=lambda case: case.name)
async def test_outbound_adapter_respects_shared_encryption_precedence(case) -> None:
    class _Bot(MessagingMixin):
        async def _send_message_transport(self, **kwargs):
            return kwargs

    bot = _Bot()
    token = bot._set_reply_encryption_context(case.inherited)
    try:
        transport_args = await bot.bot_send_message(
            mto="alice@example.test", mbody="reply", mtype="chat", encrypted=case.explicit
        )
    finally:
        bot._reset_reply_encryption_context(token)
    assert transport_args["encrypted"] is case.effective


def test_strict_omemo_jid_uses_core_validation() -> None:
    adapter = OmemoCoreMixin()
    assert adapter._bare_jid("Alice@Example.test/Phone") == "alice@example.test"
    with pytest.raises(ValueError, match="valid bare JID"):
        adapter._bare_jid(" ")

@pytest.mark.asyncio
async def test_explicit_omemo_never_downgrades_if_backend_unavailable() -> None:
    class _Bot(MessagingMixin):
        omemo_plaintext_fallback = False

        def __init__(self) -> None:
            self.plain_sent = False

        def _should_encrypt_message(self, **_kwargs) -> bool:
            return False  # e.g. optional OMEMO dependency is unavailable

        def send_message(self, **_kwargs) -> None:
            self.plain_sent = True

    bot = _Bot()
    assert await bot.bot_send_message(
        mto="alice@example.test", mbody="secret", mtype="chat", encrypted=True
    ) is None
    assert not bot.plain_sent
    with pytest.raises(RuntimeError, match="unavailable"):
        await bot._send_message_transport(
            mto="alice@example.test", mbody="secret", mtype="chat",
            encrypted=True, raise_on_failure=True,
        )
    assert not bot.plain_sent


@pytest.mark.parametrize("case", CONFIG_CASES, ids=lambda case: case.name)
def test_config_diff_adapter_redacts_secrets(case) -> None:
    host = SimpleNamespace(CONFIG_KEYS=tuple(case.before))
    lines = ConfigRuntimeMixin._format_config_changes(host, case.before, case.after)
    assert lines
    assert case.secret not in "\n".join(lines)
    assert "<redacted>" in "\n".join(lines)


def test_task_snapshot_uses_the_canonical_shared_model() -> None:
    from envs_xmpp_core.runtime import TaskInfo as SharedTaskInfo

    from banbot.task_supervisor import TaskInfo as BotTaskInfo

    assert BotTaskInfo is SharedTaskInfo
    item = BotTaskInfo(
        scope="_core", name="unban-worker", status="running", created_at="2026-09-30T00:00:00+00:00",
        done_at=None, cancelled=False, last_error=None,
    )
    assert item.identity == ("_core", "unban-worker")
    assert item.plugin == item.group == "_core"


@pytest.mark.asyncio
async def test_explicit_plaintext_fallback_stays_opt_in() -> None:
    class _Bot(MessagingMixin):
        omemo_plaintext_fallback = True
        plain_sent = False

        def _should_encrypt_message(self, **_kwargs) -> bool:
            return False

        def send_message(self, **_kwargs) -> bool:
            self.plain_sent = True
            return True

    bot = _Bot()
    assert await bot.bot_send_message(
        mto="alice@example.test", mbody="notice", mtype="chat", encrypted=True
    ) is True
    assert bot.plain_sent is True
