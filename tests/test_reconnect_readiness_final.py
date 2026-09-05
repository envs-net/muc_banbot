from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from banbot import muc as muc_module
from banbot import runtime_watchdog as watchdog_module


class ReconnectFixture(muc_module.MucMixin):
    def __init__(self) -> None:
        self.reconnecting = True
        self.reconnect_task = None
        self.reconnect_success_event = None
        self._shutdown_in_progress = False
        self._shutdown_complete = False
        self.occupants = {"room@example.org": {"Nick": {}}}
        self.bot_admin_state = {"room@example.org": True}
        self.room_join_time = {"room@example.org": 1.0}
        self.room_bot_nicks = {"room@example.org": "BanBot"}
        self.room_join_events = {"room@example.org": asyncio.Event()}
        self.events: list[str] = []

    def disconnect(self, **_kwargs):
        self.events.append("disconnect")
        return None


@pytest.mark.asyncio
async def test_initial_connection_failed_uses_slixmpp_retry_without_parallel_loop() -> None:
    bot = ReconnectFixture()
    bot.reconnecting = False
    bot._startup_completed_once = False
    bot._session_start_received = False
    timeout_arms: list[str] = []
    bot.runtime_watchdog = SimpleNamespace(
        arm_startup_timeout_extension=lambda: timeout_arms.append("armed")
    )

    original_occupants = dict(bot.occupants)
    original_admin_state = dict(bot.bot_admin_state)
    original_join_time = dict(bot.room_join_time)

    await bot.on_connection_failed(None)

    assert bot.reconnecting is False
    assert bot.reconnect_task is None
    assert bot.occupants == original_occupants
    assert bot.bot_admin_state == original_admin_state
    assert bot.room_join_time == original_join_time
    assert timeout_arms == ["armed"]


@pytest.mark.asyncio
async def test_new_disconnect_replaces_stale_reconnect_waiter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bot = ReconnectFixture()
    bot.reconnecting = False
    stale_blocker = asyncio.Event()
    replacement_started = asyncio.Event()

    async def stale_reconnect() -> None:
        await stale_blocker.wait()

    async def replacement_reconnect() -> None:
        replacement_started.set()

    stale_task = asyncio.create_task(stale_reconnect())
    bot.reconnect_task = stale_task
    monkeypatch.setattr(bot, "_delayed_reconnect", replacement_reconnect)

    await bot.on_disconnect(None)
    replacement_task = bot.reconnect_task

    assert replacement_task is not None
    assert replacement_task is not stale_task
    assert bot.reconnecting is True

    await asyncio.sleep(0)
    assert stale_task.cancelled()
    await asyncio.wait_for(replacement_started.wait(), timeout=1)
    replacement_result = await replacement_task
    assert replacement_result is None


@pytest.mark.asyncio
async def test_reconnect_timeout_disconnects_partial_session_before_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bot = ReconnectFixture()
    wait_calls = 0

    async def no_sleep(_delay: float) -> None:
        return None

    async def fake_wait_for(awaitable, *, timeout: float):
        nonlocal wait_calls
        wait_calls += 1
        if wait_calls == 1:
            close = getattr(awaitable, "close", None)
            if callable(close):
                close()
            raise asyncio.TimeoutError
        return await awaitable

    def connect_with_config() -> bool:
        bot.events.append("connect")
        if bot.events.count("connect") == 2:
            bot._get_reconnect_success_event().set()
        return True

    monkeypatch.setattr(muc_module.asyncio, "sleep", no_sleep)
    monkeypatch.setattr(muc_module.asyncio, "wait_for", fake_wait_for)
    bot.connect_with_config = connect_with_config

    await bot._delayed_reconnect()

    assert bot.events == ["connect", "disconnect", "connect"]
    assert wait_calls == 2
    assert bot.occupants == {}
    assert bot.bot_admin_state == {}
    assert bot.room_join_time == {}
    assert bot.room_bot_nicks == {}
    assert bot.room_join_events == {}


@pytest.mark.asyncio
async def test_reconnect_false_result_retries_without_startup_wait(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bot = ReconnectFixture()
    wait_calls = 0
    connect_calls = 0

    async def no_sleep(_delay: float) -> None:
        return None

    async def tracked_wait_for(awaitable, *, timeout: float):
        nonlocal wait_calls
        wait_calls += 1
        return await awaitable

    def connect_with_config() -> bool:
        nonlocal connect_calls
        connect_calls += 1
        if connect_calls == 1:
            return False
        bot._get_reconnect_success_event().set()
        return True

    monkeypatch.setattr(muc_module.asyncio, "sleep", no_sleep)
    monkeypatch.setattr(muc_module.asyncio, "wait_for", tracked_wait_for)
    bot.connect_with_config = connect_with_config

    await bot._delayed_reconnect()

    assert connect_calls == 2
    assert wait_calls == 1
    assert bot.events == []


@pytest.mark.asyncio
async def test_systemd_ready_is_deferred_until_health_worker_and_reconnect_complete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bot = SimpleNamespace(health_check_task=None, reconnecting=True)
    watchdog = watchdog_module.RuntimeWatchdog(bot)
    notifications: list[str] = []

    def record_notify(payload: str) -> bool:
        notifications.append(payload)
        return True

    monkeypatch.setattr(watchdog_module, "sd_notify", record_notify)

    assert watchdog.notify_ready() is False
    assert notifications == []

    bot.health_check_task = object()
    bot.reconnecting = False
    await asyncio.sleep(0)

    assert len(notifications) == 1
    assert notifications[0].startswith("READY=1\nSTATUS=")


@pytest.mark.asyncio
async def test_systemd_ready_is_not_sent_if_health_worker_never_starts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bot = SimpleNamespace(health_check_task=None, reconnecting=False)
    watchdog = watchdog_module.RuntimeWatchdog(bot)
    notifications: list[str] = []

    def record_notify(payload: str) -> bool:
        notifications.append(payload)
        return True

    monkeypatch.setattr(watchdog_module, "sd_notify", record_notify)

    assert watchdog.notify_ready() is False
    await asyncio.sleep(0)

    assert notifications == []
