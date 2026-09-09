"""Durable operational-message delivery for BanBot."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import aiosqlite
from envs_xmpp_core.runtime.diagnostics import exception_summary
from envs_xmpp_core.storage.outbox import (
    AsyncConnectionOutboxDatabase,
    OutboxCapacityError,
    OutboxStore,
    retry_delay_seconds,
)

log = logging.getLogger(__name__)


class OutboxMixin:
    """Persist proactive messages and retry them until delivered or dead."""

    def init_outbox_state(self) -> None:
        self.outbox_connection: aiosqlite.Connection | None = None
        self.outbox_store: OutboxStore | None = None
        self.outbox_task: asyncio.Task[Any] | None = None
        self.outbox_wakeup = asyncio.Event()
        self.outbox_delivered = 0
        self.outbox_failed_attempts = 0
        self.outbox_dead_letters = 0
        self.outbox_capacity_rejections = 0
        self.outbox_last_error: str | None = None

    async def setup_outbox_storage(self, path: str) -> None:
        """Open a dedicated SQLite connection and initialize the shared queue."""
        await self.close_outbox_storage()
        connection = await aiosqlite.connect(path)
        connection.row_factory = aiosqlite.Row
        await connection.execute("PRAGMA busy_timeout = 5000")
        adapter = AsyncConnectionOutboxDatabase(connection)
        store = OutboxStore(adapter)
        try:
            await store.init()
            await store.recover_inflight(older_than_seconds=0)
        except BaseException:
            await connection.close()
            raise
        self.outbox_connection = connection
        self.outbox_store = store
        self.outbox_wakeup = asyncio.Event()

    async def close_outbox_storage(self) -> None:
        connection = getattr(self, "outbox_connection", None)
        self.outbox_store = None
        self.outbox_connection = None
        if connection is not None:
            await connection.close()

    async def enqueue_durable_message(
        self,
        *,
        destination: str,
        body: str,
        message_type: str,
        category: str = "message",
        dedupe_key: str | None = None,
        max_attempts: int | None = None,
    ) -> int | None:
        """Persist a message and wake the retry worker."""
        if not bool(getattr(self, "outbox_enabled", True)):
            return None
        store = getattr(self, "outbox_store", None)
        if store is None:
            return None
        try:
            message_id = await store.enqueue(
                destination=destination,
                body=body,
                message_type=message_type,
                category=category,
                dedupe_key=dedupe_key,
                max_attempts=(
                    int(max_attempts)
                    if max_attempts is not None
                    else int(getattr(self, "outbox_max_attempts", 12) or 12)
                ),
                max_pending=int(getattr(self, "outbox_max_pending", 10000) or 10000),
                max_bytes=int(
                    getattr(self, "outbox_max_bytes", 50 * 1024 * 1024)
                    or 50 * 1024 * 1024
                ),
                max_per_destination=int(
                    getattr(self, "outbox_max_per_destination", 1000) or 1000
                ),
                max_per_category=int(
                    getattr(self, "outbox_max_per_category", 5000) or 5000
                ),
            )
        except OutboxCapacityError as exc:
            self.outbox_capacity_rejections += 1
            summary = exception_summary(exc)
            self.outbox_last_error = f"{type(exc).__name__}: {summary}"
            log.error("Outbox capacity rejected %s message: %s", category, summary)
            return None
        self.outbox_wakeup.set()
        return message_id

    def _outbox_retry_delay(self, attempts: int) -> int:
        return retry_delay_seconds(
            attempts,
            initial=int(getattr(self, "outbox_retry_initial_seconds", 30) or 30),
            maximum=int(getattr(self, "outbox_retry_max_seconds", 1800) or 1800),
        )

    async def run_outbox_once(self) -> int:
        """Attempt one bounded batch and return the number of claimed rows."""
        store = getattr(self, "outbox_store", None)
        if store is None or not bool(getattr(self, "outbox_enabled", True)):
            return 0
        batch = await store.claim_due(
            limit=max(1, int(getattr(self, "outbox_batch_size", 20) or 20))
        )
        for queued in batch:
            try:
                await self._send_message_transport(
                    mto=queued.destination,
                    mbody=queued.body,
                    mtype=queued.message_type,
                    encrypted=None,
                    raise_on_failure=True,
                )
            except asyncio.CancelledError:
                await store.defer(
                    queued.id,
                    retry_delay_seconds=1,
                    reason="worker cancelled",
                )
                raise
            except Exception as exc:  # noqa: BLE001 - transport retry boundary
                self.outbox_failed_attempts += 1
                summary = exception_summary(exc)
                self.outbox_last_error = f"{type(exc).__name__}: {summary}"
                dead = await store.mark_failed(
                    queued,
                    exc,
                    retry_delay_seconds=self._outbox_retry_delay(queued.attempts),
                )
                if dead:
                    self.outbox_dead_letters += 1
                    log.error(
                        "Outbox message %s became dead after %s attempt(s): %s",
                        queued.id,
                        queued.max_attempts,
                        summary,
                    )
                else:
                    log.warning("Outbox delivery %s failed: %s", queued.id, summary)
                continue
            await store.mark_sent(queued.id)
            self.outbox_delivered += 1
            self.outbox_last_error = None
        return len(batch)

    async def outbox_worker(self) -> None:
        """Retry durable messages until the reconnect-scoped task is cancelled."""
        while True:
            heartbeat = getattr(getattr(self, "tasks", None), "heartbeat", None)
            if callable(heartbeat):
                heartbeat("_core", "outbox-worker")
            await self.run_outbox_once()
            timeout = max(1.0, float(getattr(self, "outbox_poll_seconds", 5) or 5))
            try:
                await asyncio.wait_for(self.outbox_wakeup.wait(), timeout=timeout)
            except TimeoutError:
                pass
            self.outbox_wakeup.clear()

    async def outbox_runtime_state(self) -> dict[str, Any]:
        """Return compact queue state for status/health output."""
        store = getattr(self, "outbox_store", None)
        counts = (
            await store.counts()
            if store is not None
            else {"pending": 0, "inflight": 0, "dead": 0, "total": 0}
        )
        return {
            **counts,
            "delivered": int(getattr(self, "outbox_delivered", 0)),
            "failed_attempts": int(getattr(self, "outbox_failed_attempts", 0)),
            "capacity_rejections": int(getattr(self, "outbox_capacity_rejections", 0)),
            "last_error": getattr(self, "outbox_last_error", None),
        }
