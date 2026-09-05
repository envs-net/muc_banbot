from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from banbot import runtime_watchdog


def test_systemd_watchdog_interval_uses_half_timeout(monkeypatch):
    monkeypatch.setenv("WATCHDOG_USEC", "60000000")
    assert runtime_watchdog.systemd_watchdog_interval(20) == 20

    monkeypatch.setenv("WATCHDOG_USEC", "10000000")
    assert runtime_watchdog.systemd_watchdog_interval(20) == 5


def test_notify_ready_reports_complete_startup(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(runtime_watchdog, "sd_notify", lambda payload: calls.append(payload) or True)
    watchdog = runtime_watchdog.RuntimeWatchdog(SimpleNamespace())

    assert watchdog.notify_ready() is True
    assert calls == ["READY=1\nSTATUS=muc_banbot startup complete"]


@pytest.mark.asyncio
async def test_systemd_watchdog_forces_runtime_worker_even_if_config_disabled(monkeypatch):
    monkeypatch.setenv("NOTIFY_SOCKET", "/tmp/example-notify.sock")
    monkeypatch.setenv("WATCHDOG_USEC", "60000000")

    started: list[tuple[str, str]] = []

    class DummyTask:
        def __init__(self):
            self.cancelled = False

        def done(self):
            return self.cancelled

        def cancel(self):
            self.cancelled = True

        def __await__(self):
            async def complete():
                return None
            return complete().__await__()

    class Supervisor:
        def create_resilient(self, group, factory, *, name, service):
            factory().close()
            started.append((group, name))
            return DummyTask()

    bot = SimpleNamespace(watchdog_enabled=False, tasks=Supervisor())
    watchdog = runtime_watchdog.RuntimeWatchdog(bot)
    monkeypatch.setattr(runtime_watchdog, "sd_notify", lambda _payload: True)

    await watchdog.start()
    assert watchdog.state.enabled is True
    assert watchdog.state.systemd_active is True
    assert watchdog.state.worker_running is True
    assert started == [("_runtime", "runtime-watchdog")]

    await watchdog.stop()
    assert watchdog.state.worker_running is False


@pytest.mark.asyncio
async def test_watchdog_stop_uses_supervisor_bounded_cancellation(monkeypatch):
    class DummyTask:
        def done(self):
            return False

        def cancel(self):  # pragma: no cover - supervisor owns cancellation
            raise AssertionError("watchdog must not cancel a supervisor-owned task directly")

    task = DummyTask()

    class Supervisor:
        def __init__(self):
            self.calls = []

        def owns(self, candidate):
            return candidate is task

        async def cancel_group(self, group, *, timeout):
            self.calls.append((group, timeout))
            return 1

    supervisor = Supervisor()
    bot = SimpleNamespace(tasks=supervisor)
    watchdog = runtime_watchdog.RuntimeWatchdog(bot)
    watchdog.task = task
    watchdog.state.worker_running = True
    monkeypatch.setattr(runtime_watchdog, "sd_notify", lambda _payload: True)

    await watchdog.stop()

    assert supervisor.calls == [("_runtime", 5.0)]
    assert watchdog.state.worker_running is False

@pytest.mark.asyncio
async def test_startup_timeout_extension_keeps_waiting_service_alive(monkeypatch):
    calls: list[str] = []
    bot = SimpleNamespace(_session_start_received=False)
    watchdog = runtime_watchdog.RuntimeWatchdog(bot)
    real_sleep = runtime_watchdog.asyncio.sleep

    monkeypatch.setenv("NOTIFY_SOCKET", "/tmp/example-notify.sock")
    monkeypatch.setattr(
        runtime_watchdog,
        "sd_notify",
        lambda payload: calls.append(payload) or True,
    )

    sleep_calls = 0

    async def controlled_sleep(_delay):
        nonlocal sleep_calls
        sleep_calls += 1
        if sleep_calls >= 2:
            bot._session_start_received = True
        await real_sleep(0)

    monkeypatch.setattr(runtime_watchdog.asyncio, "sleep", controlled_sleep)

    assert watchdog.arm_startup_timeout_extension(asyncio.get_running_loop()) is True
    await real_sleep(0)
    await real_sleep(0)
    await real_sleep(0)

    assert calls
    assert calls[0] == (
        "EXTEND_TIMEOUT_USEC=300000000\n"
        "STATUS=muc_banbot waiting for XMPP session"
    )
    assert any(call.startswith("EXTEND_TIMEOUT_USEC=") for call in calls)
    assert watchdog.startup_timeout_task is None


@pytest.mark.asyncio
async def test_ready_cancels_startup_timeout_extension(monkeypatch):
    calls: list[str] = []
    bot = SimpleNamespace(_session_start_received=False)
    watchdog = runtime_watchdog.RuntimeWatchdog(bot)

    monkeypatch.setenv("NOTIFY_SOCKET", "/tmp/example-notify.sock")
    monkeypatch.setattr(
        runtime_watchdog,
        "sd_notify",
        lambda payload: calls.append(payload) or True,
    )

    assert watchdog.arm_startup_timeout_extension(asyncio.get_running_loop()) is True
    task = watchdog.startup_timeout_task
    assert task is not None

    assert watchdog.notify_ready() is True
    assert watchdog.ready_sent is True
    assert watchdog.startup_timeout_task is None

    await asyncio.sleep(0)
    assert task.cancelled() or task.done()
    assert calls[-1].startswith("READY=1\nSTATUS=")
