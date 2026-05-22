"""Redaction index and command tests."""

from __future__ import annotations

import time
from xml.etree import ElementTree as ET

import pytest

aiosqlite = pytest.importorskip("aiosqlite")

from banbot.db import DatabaseMixin
from banbot.redaction import RedactionMixin, SID_NS


class FakeFrom:
    def __init__(self, bare: str):
        self.bare = bare


class FakeMessage(dict):
    def __init__(self, room: str, nick: str, stanza_id: str | None = None, body: str = "hello"):
        super().__init__()
        self["from"] = FakeFrom(room)
        self["mucnick"] = nick
        self["body"] = body
        self["id"] = "client-id"
        self.xml = ET.Element("message")
        if stanza_id:
            ET.SubElement(
                self.xml,
                f"{{{SID_NS}}}stanza-id",
                {"by": room, "id": stanza_id},
            )


class FakeOutgoingMessage:
    def __init__(self, sent):
        self.sent = sent
        self.children = []
        self.kwargs = {}

    def append(self, element):
        self.children.append(element)

    def send(self):
        self.sent.append(self)


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

    bare_jid = staticmethod(lambda jid: jid.split("/", 1)[0].lower() if jid else None)

    async def bot_send_message(self, **kwargs):
        self.sent.append(kwargs)

    def make_message(self, **kwargs):
        msg = FakeOutgoingMessage(self.redaction_stanzas)
        msg.kwargs = kwargs
        return msg


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
        assert bot.redaction_stanzas[0].kwargs["mto"] == "room@conference.example.test"
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
