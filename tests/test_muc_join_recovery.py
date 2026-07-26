"""Dependency-light tests for MUC join recovery and health-check rejoins."""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import pytest

from banbot.health_check import HealthCheckMixin
from banbot.muc import MucMixin
from banbot.occupants import BotOccupantMixin
from banbot.utils import bare_jid


ROOM_JID = "room@conference.example.test"
BOT_NICK = "BanBot"
BOT_ALT_NICK = "BanBot-alt"
BOT_BARE_JID = "bot@example.org"
BOT_FULL_JID = f"{BOT_BARE_JID}/new-resource"


class SelfPresence:
    def __init__(self, room: str = ROOM_JID) -> None:
        self._from = SimpleNamespace(bare=room, resource=BOT_ALT_NICK)
        self._muc = {
            "nick": BOT_ALT_NICK,
            "jid": BOT_FULL_JID,
            "affiliation": "admin",
            "role": "moderator",
            "status_codes": {"110", "210"},
        }
        self.xml = None

    def __getitem__(self, key):
        if key == "from":
            return self._from
        if key == "muc":
            return self._muc
        raise KeyError(key)


class SubjectlessJoinPlugin:
    def __init__(self, bot) -> None:
        self.bot = bot
        self.wait_calls = []
        self.legacy_calls = []
        self.leave_calls = []
        self.waiter_cancelled = False

    async def join_muc_wait(self, room, nick, **kwargs):
        self.wait_calls.append((room, nick, kwargs))
        await asyncio.sleep(0)
        await self.bot.on_muc_presence(SelfPresence(room))
        try:
            # Simulate a MUC that confirms self-presence but never sends a room
            # subject. BanBot must still treat the join as successful.
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.waiter_cancelled = True
            raise

    def join_muc(self, room, nick):
        self.legacy_calls.append((room, nick))
        raise AssertionError("legacy join_muc() must not be used")

    def leave_muc(self, room, nick):
        self.leave_calls.append((room, nick))


class HealthRejoinBot(MucMixin, HealthCheckMixin):
    bare_jid = staticmethod(bare_jid)

    def __init__(self, room: str = ROOM_JID) -> None:
        self.protected_rooms = {room}
        self.occupants = {}
        self.room_bot_nicks = {}
        self.room_join_events = {}
        self.room_join_time = {}
        self.bot_admin_state = {}
        self.boundjid = SimpleNamespace(bare=BOT_BARE_JID)
        self.plugin = {"xep_0045": SubjectlessJoinPlugin(self)}
        self.reconnecting = False
        self.reconnect_task = None
        self.last_audit_cleanup_run = time.time()
        self.successes = []
        self.alerts = []

    async def get_db_stats(self):
        return {"db_size_bytes": 0}

    async def cleanup_old_audit_logs(self):
        self.last_audit_cleanup_run = time.time()
        return 0

    def record_alert_success(self, key):
        self.successes.append(key)

    async def send_operational_alert(self, key, title, message, **kwargs):
        self.alerts.append((key, title, message, kwargs))
        return True

    def is_bot_admin_or_owner(self, room):
        _nick, info = self._bot_occupant_entry(room)
        return bool(info and info.get("affiliation") in ("admin", "owner"))

    async def verify_admin_rights(self, room):
        return self.is_bot_admin_or_owner(room)


class FailingJoinPlugin:
    def __init__(self) -> None:
        self.calls = 0
        self.leave_calls = []

    async def join_muc_wait(self, room, nick, **kwargs):
        self.calls += 1
        await asyncio.sleep(0)
        raise asyncio.TimeoutError("no self presence")

    def join_muc(self, room, nick):
        raise AssertionError("legacy join_muc() must not be used")

    def leave_muc(self, room, nick):
        self.leave_calls.append((room, nick))


def test_muc_uses_shared_bot_occupant_lookup():
    assert "_bot_occupant_entry" not in MucMixin.__dict__
    assert MucMixin._bot_occupant_entry is BotOccupantMixin._bot_occupant_entry


@pytest.mark.asyncio
async def test_health_check_cycle_rejoins_via_wait_api_and_recovers_admin_state(monkeypatch):
    monkeypatch.setattr("banbot.health_check.ADMIN_ROOM", ROOM_JID)
    bot = HealthRejoinBot()

    await bot._run_health_check_cycle()

    muc_plugin = bot.plugin["xep_0045"]
    assert muc_plugin.leave_calls == [(ROOM_JID, BOT_NICK)]
    assert muc_plugin.legacy_calls == []
    assert muc_plugin.wait_calls == [
        (ROOM_JID, BOT_NICK, {"maxstanzas": 0, "timeout": 20.0})
    ]
    assert muc_plugin.waiter_cancelled is True
    assert bot._bot_occupant_entry(ROOM_JID)[0] == BOT_ALT_NICK
    assert bot.is_bot_admin_or_owner(ROOM_JID) is True
    assert bot.alerts == []
    assert f"health_not_in_room:{ROOM_JID}" in bot.successes
    assert f"health_check_error:{ROOM_JID}" in bot.successes


@pytest.mark.asyncio
async def test_failed_wait_join_is_retried_and_consumed_without_orphaned_task():
    bot = HealthRejoinBot()
    plugin = FailingJoinPlugin()
    bot.plugin = {"xep_0045": plugin}
    loop = asyncio.get_running_loop()
    orphaned_errors = []
    previous_handler = loop.get_exception_handler()
    loop.set_exception_handler(lambda _loop, context: orphaned_errors.append(context))

    try:
        joined = await bot.ensure_muc_joined(ROOM_JID, timeout=0.1, retries=2)
        await asyncio.sleep(0)
    finally:
        loop.set_exception_handler(previous_handler)

    assert joined is False
    assert plugin.calls == 2
    assert ROOM_JID not in bot.room_join_time
    assert ROOM_JID not in bot.room_join_events
    assert orphaned_errors == []

@pytest.mark.asyncio
async def test_join_uses_runtime_timeout_and_retry_settings():
    bot = HealthRejoinBot()
    plugin = FailingJoinPlugin()
    plugin.kwargs = []

    async def failing_join(room, nick, **kwargs):
        plugin.calls += 1
        plugin.kwargs.append(kwargs)
        raise asyncio.TimeoutError()

    plugin.join_muc_wait = failing_join
    bot.plugin = {"xep_0045": plugin}
    bot.muc_join_timeout_seconds = 37
    bot.muc_join_retries = 3

    joined = await bot.ensure_muc_joined(ROOM_JID)

    assert joined is False
    assert plugin.calls == 3
    assert plugin.kwargs == [
        {"maxstanzas": 0, "timeout": 37.0},
        {"maxstanzas": 0, "timeout": 37.0},
        {"maxstanzas": 0, "timeout": 37.0},
    ]
