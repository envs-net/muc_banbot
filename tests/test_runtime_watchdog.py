from __future__ import annotations

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
