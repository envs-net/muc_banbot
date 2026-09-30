"""BanBot's MUC adapter must only mark verified self-presence as joined."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from envs_xmpp_core.xmpp.muc_join import MucJoinResult

from banbot import muc as muc_module


class MinimalRoomBot(muc_module.MucMixin):
    def __init__(self) -> None:
        self.plugin = {"xep_0045": object()}
        self.room_join_events: dict[str, asyncio.Event] = {}
        self.room_bot_nicks: dict[str, str] = {}
        self.occupants: dict[str, dict] = {}
        self.room_join_time: dict[str, float] = {}
        self.boundjid = SimpleNamespace(bare="bot@example.test")
        self.bot_admin_state: dict[str, bool] = {}


@pytest.mark.asyncio
async def test_confirmed_muc_join_and_reconnect_invalidation(monkeypatch) -> None:
    bot = MinimalRoomBot()
    room = "room@conference.example.test"

    async def fake_join(_muc, target, _nick, **kwargs):
        assert target == room
        assert kwargs["is_joined"]() is False
        assert bot._room_lifecycle_registry().get(room).state == "joining"
        bot.occupants[room] = {"NewNick": {"jid": "bot@example.test"}}
        bot.room_bot_nicks[room] = "NewNick"
        return MucJoinResult(joined=True, api_name="join_muc_wait", attempts=1)

    monkeypatch.setattr(muc_module, "join_muc_confirmed", fake_join)
    assert await bot.ensure_muc_joined(room, nick="Requested") is True
    state = bot._room_lifecycle_registry().get(room)
    assert state is not None and state.joined and state.nick == "NewNick"
    generation = state.generation
    bot._room_lifecycle_registry().new_session()
    assert bot._room_lifecycle_registry().get(room).state == "configured"
    assert bot._room_lifecycle_registry().confirm_self_presence(
        room, "OldNick", generation=generation
    ) is None


@pytest.mark.asyncio
async def test_unconfirmed_join_is_failure_not_joined(monkeypatch) -> None:
    bot = MinimalRoomBot()
    room = "room@conference.example.test"

    async def fake_join(_muc, _room, _nick, **_kwargs):
        return MucJoinResult(
            joined=False, api_name="join_muc_wait", attempts=1, error=TimeoutError("no self-presence")
        )

    monkeypatch.setattr(muc_module, "join_muc_confirmed", fake_join)
    assert await bot.ensure_muc_joined(room) is False
    state = bot._room_lifecycle_registry().get(room)
    assert state is not None and state.state == "failed" and not state.joined


def test_other_occupants_never_create_joined_state() -> None:
    bot = MinimalRoomBot()
    room = "room@conference.example.test"
    bot._room_lifecycle_registry().configure(room)
    bot.occupants[room] = {"SomeoneElse": {"jid": "user@example.test"}}
    assert not bot._bot_occupant_entry(room)[1]
    assert bot._room_lifecycle_registry().get(room).state == "configured"
