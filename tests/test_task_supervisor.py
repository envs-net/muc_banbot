from __future__ import annotations

import asyncio

import pytest

from banbot.task_supervisor import TaskSupervisor


@pytest.mark.asyncio
async def test_resilient_service_restarts_after_failure(monkeypatch):
    supervisor = TaskSupervisor()
    attempts = 0
    running = asyncio.Event()
    real_sleep = asyncio.sleep

    async def fast_sleep(_delay: float) -> None:
        await real_sleep(0)

    monkeypatch.setattr(asyncio, "sleep", fast_sleep)

    async def worker() -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("boom")
        running.set()
        await asyncio.Event().wait()

    task = supervisor.create_resilient("_core", worker, name="test-worker")
    await asyncio.wait_for(running.wait(), timeout=1)

    info = supervisor.snapshot(include_done=False)[0]
    assert info.name == "test-worker"
    assert info.status == "running"
    assert info.restart_count == 1
    assert attempts == 2

    assert await supervisor.cancel_group("_core") == 1
    assert task.cancelled()


@pytest.mark.asyncio
async def test_resilient_service_exposes_terminal_failure():
    supervisor = TaskSupervisor()

    async def worker() -> None:
        raise ValueError("broken")

    task = supervisor.create_resilient(
        "_core",
        worker,
        name="terminal-worker",
        max_restarts=0,
    )
    with pytest.raises(RuntimeError, match="exceeded restart limit"):
        result = await task
        assert result is None

    info = supervisor.snapshot()[0]
    assert info.status == "failed"
    assert info.restart_count == 1
    assert "exceeded restart limit" in (info.last_error or "")
