"""BanBot operator inventory consumes the shared lifecycle contract."""

from __future__ import annotations

from types import SimpleNamespace

from envs_xmpp_core.runtime import RoomLifecycleRegistry

from banbot.occupants import BotOccupantMixin
from banbot.status import StatusMixin


def test_status_room_lifecycle_does_not_invent_self_presence(monkeypatch):
    registry = RoomLifecycleRegistry()
    room = "room@conference.example.test"
    registry.confirm_self_presence(room, "Bot")
    monkeypatch.setattr(
        BotOccupantMixin,
        "_bot_occupant_entry",
        lambda _host, _room: (None, None),
    )
    host = SimpleNamespace(room_lifecycle=registry)
    view = StatusMixin._status_room_views(host, [room])[0]
    assert not view.joined
    assert view.state == "attention"
    assert view.needs_attention
    assert "lifecycle=joined" in view.details


def test_status_room_lifecycle_leaving_is_expected(monkeypatch):
    registry = RoomLifecycleRegistry()
    room = "room@conference.example.test"
    registry.begin_leave(room)
    monkeypatch.setattr(
        BotOccupantMixin,
        "_bot_occupant_entry",
        lambda _host, _room: (None, None),
    )
    host = SimpleNamespace(room_lifecycle=registry)
    view = StatusMixin._status_room_views(host, [room])[0]
    assert view.state == "leaving"
    assert not view.needs_attention


def test_health_warns_about_failed_lifecycle_without_changing_auth_policy():
    from banbot.status_health import _rooms_check

    registry = RoomLifecycleRegistry()
    room = "failed@conference.example.test"
    registry.mark_failed(room)
    bot = SimpleNamespace(
        room_lifecycle=registry,
        protected_rooms={room},
        bot_admin_state={room: True},
        occupants={},
        bare_jid=lambda jid: jid,
        safe_jid=lambda jid: jid,
        admin_affiliation_query_forbidden_rooms=set(),
    )
    health = _rooms_check(bot)
    assert any("Room lifecycle needs attention" in message for message in health.data["warnings"])
