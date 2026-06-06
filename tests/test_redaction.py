"""Redaction index and command tests."""

from __future__ import annotations

import asyncio
import time
from xml.etree import ElementTree as ET

import pytest

aiosqlite = pytest.importorskip("aiosqlite")

from banbot.db import DatabaseMixin
from banbot.redaction import RedactionMixin, SID_NS


class FakeFrom:
    def __init__(self, bare: str):
        self.bare = bare


class FakeMessage:
    def __init__(self, room: str, nick: str, stanza_id: str | None = None, body: str = "hello"):
        self.room = room
        self.nick = nick
        self.stanza_id = stanza_id
        self.body = body
        self.xml = ET.Element("message")
        self._data = {
            "from": FakeFrom(room),
            "mucnick": nick,
            "body": body,
            "id": "client-id",
        }

        if stanza_id:
            ET.SubElement(
                self.xml,
                f"{{{SID_NS}}}stanza-id",
                {"by": room, "id": stanza_id},
            )

    def get(self, key, default=None):
        return self._data.get(key, default)

    def __getitem__(self, key):
        return self._data[key]


class FakeOutgoingIq:
    def __init__(
        self,
        sent,
        delay: float = 0.0,
        fail: bool = False,
        fail_error: str | None = None,
        inflight_tracker: dict[str, int] | None = None,
    ):
        self.sent = sent
        self.children = []
        self.kwargs = {}
        self.delay = delay
        self.fail = fail
        self.fail_error = fail_error or "simulated retract failure"
        self.inflight_tracker = inflight_tracker

    def append(self, element):
        self.children.append(element)

    async def send(self, timeout=None):
        self.timeout = timeout
        if self.inflight_tracker is not None:
            self.inflight_tracker["current"] += 1
            self.inflight_tracker["max"] = max(
                self.inflight_tracker["max"],
                self.inflight_tracker["current"],
            )

        try:
            if self.delay:
                await asyncio.sleep(self.delay)
            if self.fail:
                raise RuntimeError(self.fail_error)
            self.sent.append(self)
            return self
        finally:
            if self.inflight_tracker is not None:
                self.inflight_tracker["current"] -= 1


class RedactionBot(DatabaseMixin, RedactionMixin):
    def __init__(self):
        self.redaction_enabled = True
        self.redaction_index_retention_days = 30
        self.redaction_auto_reasons = ["spam", "harassment"]
        self.protected_rooms = {"room@conference.example.test"}
        self.occupants = {
            "room@conference.example.test": {
                "Alice": {"jid": "alice@example.org/resource"},
            }
        }
        self.sent = []
        self.redaction_stanzas = []
        self.command_prefix = "!"
        self.redaction_retract_concurrency = 3
        self.redaction_iq_delay = 0.0
        self.fail_stanza_ids = set()
        self.fail_stanza_errors = {}
        self.redaction_inflight_tracker = {"current": 0, "max": 0}
        self.alerts = []

    bare_jid = staticmethod(lambda jid: jid.split("/", 1)[0].lower() if jid else None)

    async def bot_send_message(self, **kwargs):
        self.sent.append(kwargs)

    def make_iq_set(self, **kwargs):
        iq = FakeOutgoingIq(
            self.redaction_stanzas,
            delay=self.redaction_iq_delay,
            inflight_tracker=self.redaction_inflight_tracker,
        )
        iq.kwargs = kwargs
        original_append = iq.append

        def append_with_failure_marker(element):
            original_append(element)
            stanza_id = element.attrib.get("id")
            if stanza_id in self.fail_stanza_ids:
                iq.fail = True
                iq.fail_error = self.fail_stanza_errors.get(stanza_id, iq.fail_error)

        iq.append = append_with_failure_marker
        return iq

    async def send_operational_alert(self, key, title, message, enabled=True, details=None):
        if enabled:
            self.alerts.append((key, title, message, details or {}))


@pytest.mark.asyncio
async def test_redaction_indexes_message_with_room_stanza_id(temp_db_path):
    bot = RedactionBot()
    await bot.setup_db()
    try:
        msg = FakeMessage("room@conference.example.test", "Alice", "stanza-1")

        indexed = await bot._redaction_index_message(msg)

        assert indexed is True
        async with bot.db.execute("SELECT room_jid, sender_jid, stanza_id FROM redaction_index") as cursor:
            rows = await cursor.fetchall()
        assert rows == [("room@conference.example.test", "alice@example.org", "stanza-1")]
    finally:
        await bot.db.close()


@pytest.mark.asyncio
async def test_redact_jid_retracts_all_indexed_messages(temp_db_path):
    bot = RedactionBot()
    await bot.setup_db()
    try:
        await bot._redaction_index_message(FakeMessage("room@conference.example.test", "Alice", "stanza-1"))
        await bot._redaction_index_message(FakeMessage("room@conference.example.test", "Alice", "stanza-2"))

        await bot.cmd_redact(["alice@example.org", "spam"], "admin@conference.example.test", actor="admin@example.org")

        assert len(bot.redaction_stanzas) == 2
        assert all(stanza.kwargs["ito"] == "room@conference.example.test" for stanza in bot.redaction_stanzas)
        assert bot.redaction_stanzas[0].children[0].tag == "{urn:xmpp:message-moderate:1}moderate"
        assert "Messages found: 2" in bot.sent[-1]["mbody"]
        assert "Redacted: 2" in bot.sent[-1]["mbody"]
        async with bot.db.execute("SELECT COUNT(*) FROM redaction_index WHERE redacted_at IS NOT NULL") as cursor:
            row = await cursor.fetchone()
        assert row[0] == 2
    finally:
        await bot.db.close()


@pytest.mark.asyncio
async def test_redact_id_retracts_single_stanza(temp_db_path):
    bot = RedactionBot()
    await bot.setup_db()
    try:
        await bot.cmd_redact(
            ["id", "room@conference.example.test", "stanza-1", "spam"],
            "admin@conference.example.test",
            actor="admin@example.org",
        )

        assert len(bot.redaction_stanzas) == 1
        iq = bot.redaction_stanzas[0]
        assert iq.kwargs["ito"] == "room@conference.example.test"
        assert iq.timeout == 10
        assert iq.children[0].tag == "{urn:xmpp:message-moderate:1}moderate"
        assert iq.children[0].attrib["id"] == "stanza-1"
        assert iq.children[0].find("{urn:xmpp:message-retract:1}retract") is not None
        assert "Stanza ID: stanza-1" in bot.sent[-1]["mbody"]
    finally:
        await bot.db.close()


@pytest.mark.asyncio
async def test_redact_cleanup_deletes_old_entries(temp_db_path):
    bot = RedactionBot()
    bot.redaction_index_retention_days = 30
    await bot.setup_db()
    try:
        await bot.db.execute(
            """
            INSERT INTO redaction_index (room_jid, sender_jid, stanza_id, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (
                "room@conference.example.test",
                "alice@example.org",
                "old-stanza",
                int(time.time()) - 31 * 86400,
            ),
        )
        await bot.db.commit()

        await bot.cmd_redact(["cleanup"], "admin@conference.example.test", actor="admin@example.org")

        assert "Deleted entries: 1" in bot.sent[-1]["mbody"]
    finally:
        await bot.db.close()


@pytest.mark.asyncio
async def test_auto_redaction_runs_for_matching_ban_reason(temp_db_path):
    bot = RedactionBot()
    await bot.setup_db()
    try:
        await bot._redaction_index_message(FakeMessage("room@conference.example.test", "Alice", "stanza-1"))

        await bot.maybe_auto_redact_after_ban("alice@example.org", "confirmed spam", actor="admin@example.org")

        assert len(bot.redaction_stanzas) == 1
        assert "Auto-redaction completed after ban" in bot.sent[-1]["mbody"]
    finally:
        await bot.db.close()


def test_auto_reason_matching_is_case_insensitive():
    bot = RedactionBot()

    assert bot._redaction_auto_reason_matches("Confirmed SPAM wave") == "spam"
    assert bot._redaction_auto_reason_matches("ordinary moderation note") is None


@pytest.mark.asyncio
async def test_redact_rows_uses_bounded_concurrency_and_batch_marks_rows(temp_db_path):
    bot = RedactionBot()
    bot.redaction_retract_concurrency = 2
    bot.redaction_iq_delay = 0.05
    await bot.setup_db()
    try:
        for i in range(4):
            await bot._redaction_index_message(
                FakeMessage("room@conference.example.test", "Alice", f"stanza-{i}")
            )

        summary = await bot.redact_jid_messages(
            "alice@example.org",
            reason="spam",
            actor="admin@example.org",
            announce=False,
        )

        assert summary["found"] == 4
        assert summary["redacted"] == 4
        assert summary["failed"] == 0
        assert len(bot.redaction_stanzas) == 4
        assert bot.redaction_inflight_tracker["max"] == 2

        async with bot.db.execute("SELECT COUNT(*) FROM redaction_index WHERE redacted_at IS NOT NULL") as cursor:
            row = await cursor.fetchone()
        assert row[0] == 4
    finally:
        await bot.db.close()


@pytest.mark.asyncio
async def test_redact_rows_counts_failed_retractions_without_marking_rows(temp_db_path):
    bot = RedactionBot()
    bot.fail_stanza_ids = {"stanza-2"}
    await bot.setup_db()
    try:
        await bot._redaction_index_message(FakeMessage("room@conference.example.test", "Alice", "stanza-1"))
        await bot._redaction_index_message(FakeMessage("room@conference.example.test", "Alice", "stanza-2"))

        summary = await bot.redact_jid_messages(
            "alice@example.org",
            reason="spam",
            actor="admin@example.org",
            announce=False,
        )

        assert summary["found"] == 2
        assert summary["redacted"] == 1
        assert summary["failed"] == 1

        async with bot.db.execute(
            "SELECT stanza_id FROM redaction_index WHERE redacted_at IS NOT NULL"
        ) as cursor:
            rows = await cursor.fetchall()
        assert rows == [("stanza-1",)]
    finally:
        await bot.db.close()


@pytest.mark.asyncio
async def test_redact_rows_treats_already_retracted_stanzas_as_skipped(temp_db_path):
    bot = RedactionBot()
    bot.fail_stanza_ids = {"stanza-2"}
    bot.fail_stanza_errors = {"stanza-2": "item-not-found: stanza already retracted"}
    await bot.setup_db()
    try:
        await bot._redaction_index_message(FakeMessage("room@conference.example.test", "Alice", "stanza-1"))
        await bot._redaction_index_message(FakeMessage("room@conference.example.test", "Alice", "stanza-2"))

        summary = await bot.redact_jid_messages(
            "alice@example.org",
            reason="spam",
            actor="admin@example.org",
            announce=False,
        )

        assert summary["found"] == 2
        assert summary["redacted"] == 1
        assert summary["skipped"] == 1
        assert summary["failed"] == 0
        assert bot.alerts == []

        async with bot.db.execute(
            "SELECT COUNT(*) FROM redaction_index WHERE redacted_at IS NOT NULL"
        ) as cursor:
            row = await cursor.fetchone()
        assert row[0] == 2
    finally:
        await bot.db.close()
