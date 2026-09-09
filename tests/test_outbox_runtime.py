"""Durable BanBot outbox integration tests."""

from __future__ import annotations

import asyncio

import pytest

from banbot.outbox import OutboxMixin


class OutboxBot(OutboxMixin):
    def __init__(self) -> None:
        self.init_outbox_state()
        self.outbox_enabled = True
        self.outbox_retry_initial_seconds = 1
        self.outbox_retry_max_seconds = 2
        self.outbox_max_attempts = 2
        self.outbox_batch_size = 20
        self.outbox_poll_seconds = 1
        self.tasks = None
        self.sent: list[tuple[str, str, str]] = []
        self.fail = False

    async def _send_message_transport(self, *, mto, mbody, mtype, encrypted, **kwargs):
        del encrypted, kwargs
        if self.fail:
            raise RuntimeError("offline")
        self.sent.append((mto, mbody, mtype))


@pytest.mark.asyncio
async def test_outbox_persists_and_delivers(tmp_path) -> None:
    bot = OutboxBot()
    db_path = tmp_path / "banbot.db"
    await bot.setup_outbox_storage(str(db_path))
    message_id = await bot.enqueue_durable_message(
        destination="admin@example.test",
        body="alert",
        message_type="groupchat",
        category="operational_alert",
        dedupe_key="alert:test",
    )
    assert message_id is not None
    assert (await bot.outbox_runtime_state())["pending"] == 1
    assert await bot.run_outbox_once() == 1
    assert bot.sent == [("admin@example.test", "alert", "groupchat")]
    assert (await bot.outbox_runtime_state())["total"] == 0
    await bot.close_outbox_storage()


@pytest.mark.asyncio
async def test_outbox_failure_retries_then_becomes_dead(tmp_path, monkeypatch) -> None:
    bot = OutboxBot()
    bot.fail = True
    db_path = tmp_path / "banbot.db"
    await bot.setup_outbox_storage(str(db_path))
    await bot.enqueue_durable_message(
        destination="admin@example.test",
        body="alert",
        message_type="groupchat",
        max_attempts=1,
    )
    monkeypatch.setattr(bot, "_outbox_retry_delay", lambda attempts: 1)
    assert await bot.run_outbox_once() == 1
    state = await bot.outbox_runtime_state()
    assert state["dead"] == 1
    assert state["failed_attempts"] == 1
    await bot.close_outbox_storage()


@pytest.mark.asyncio
async def test_worker_is_cancellable(tmp_path) -> None:
    bot = OutboxBot()
    await bot.setup_outbox_storage(str(tmp_path / "banbot.db"))
    task = asyncio.create_task(bot.outbox_worker())
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await bot.close_outbox_storage()
