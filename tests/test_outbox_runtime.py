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

@pytest.mark.asyncio
async def test_new_outbox_worker_generation_recovers_abandoned_inflight_rows(tmp_path) -> None:
    bot = OutboxBot()
    await bot.setup_outbox_storage(str(tmp_path / "banbot.db"))
    await bot.enqueue_durable_message(
        destination="admin@example.test",
        body="after reconnect",
        message_type="groupchat",
    )
    assert bot.outbox_store is not None
    claimed = await bot.outbox_store.claim_due(limit=1)
    assert len(claimed) == 1
    assert (await bot.outbox_runtime_state())["inflight"] == 1

    assert await bot._recover_outbox_inflight(older_than_seconds=0) == 1
    state = await bot.outbox_runtime_state()
    assert state["pending"] == 1
    assert state["inflight"] == 0
    assert await bot.run_outbox_once() == 1
    assert bot.sent == [("admin@example.test", "after reconnect", "groupchat")]
    await bot.close_outbox_storage()


@pytest.mark.asyncio
async def test_outbox_cancellation_is_not_masked_when_defer_fails(tmp_path, monkeypatch) -> None:
    bot = OutboxBot()
    await bot.setup_outbox_storage(str(tmp_path / "banbot.db"))
    await bot.enqueue_durable_message(
        destination="admin@example.test",
        body="cancel me",
        message_type="groupchat",
    )
    assert bot.outbox_store is not None

    transport_started = asyncio.Event()

    async def blocked_transport(**_kwargs):
        transport_started.set()
        await asyncio.Event().wait()

    async def broken_defer(*_args, **_kwargs):
        raise RuntimeError("synthetic defer failure")

    monkeypatch.setattr(bot, "_send_message_transport", blocked_transport)
    monkeypatch.setattr(bot.outbox_store, "defer", broken_defer)

    task = asyncio.create_task(bot.run_outbox_once())
    await asyncio.wait_for(transport_started.wait(), timeout=1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert "synthetic defer failure" in str(bot.outbox_last_error)
    await bot.close_outbox_storage()

@pytest.mark.asyncio
async def test_outbox_cancellation_during_delivery_ack_requeues_claim(tmp_path, monkeypatch) -> None:
    bot = OutboxBot()
    await bot.setup_outbox_storage(str(tmp_path / "banbot.db"))
    await bot.enqueue_durable_message(
        destination="admin@example.test",
        body="ack cancellation",
        message_type="groupchat",
    )
    assert bot.outbox_store is not None

    mark_started = asyncio.Event()
    original_defer = bot.outbox_store.defer
    deferred_reasons: list[str | None] = []

    async def blocked_mark_sent(_message_id: int) -> None:
        mark_started.set()
        await asyncio.Event().wait()

    async def recording_defer(message_id: int, **kwargs) -> None:
        deferred_reasons.append(kwargs.get("reason"))
        await original_defer(message_id, **kwargs)

    monkeypatch.setattr(bot.outbox_store, "mark_sent", blocked_mark_sent)
    monkeypatch.setattr(bot.outbox_store, "defer", recording_defer)

    task = asyncio.create_task(bot.run_outbox_once())
    await asyncio.wait_for(mark_started.wait(), timeout=1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    state = await bot.outbox_runtime_state()
    assert state["pending"] == 1
    assert state["inflight"] == 0
    assert deferred_reasons == ["worker cancelled while acknowledging delivery"]
    await bot.close_outbox_storage()
