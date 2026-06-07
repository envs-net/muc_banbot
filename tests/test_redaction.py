"""Redaction index and command tests."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

REDACTION_TEST_IMPORTS_OK = True

try:
    from banbot.db import DatabaseMixin
    from banbot.redaction import (
        REDACTION_CLEANUP_INTERVAL_SECONDS,
        REDACTION_IQ_TIMEOUT_SECONDS,
        RedactionMixin,
        SID_NS,
    )
    from banbot.utils import bare_jid as normalize_bare_jid
except ImportError:
    REDACTION_TEST_IMPORTS_OK = False

    class DatabaseMixin:  # type: ignore[no-redef]
        """Fallback base class used only when redaction imports are skipped."""

        pass

    class RedactionMixin:  # type: ignore[no-redef]
        """Fallback base class used only when redaction imports are skipped."""

        pass

    # Keep these fallback defaults synchronized with banbot.redaction constants.
    _REDACTION_FALLBACK_DEFAULTS = {
        "cleanup_interval_seconds": 60,
        "iq_timeout_seconds": 10,
        "sid_ns": "urn:xmpp:sid:0",
    }

    REDACTION_CLEANUP_INTERVAL_SECONDS = (
        _REDACTION_FALLBACK_DEFAULTS["cleanup_interval_seconds"]
    )
    REDACTION_IQ_TIMEOUT_SECONDS = (
        _REDACTION_FALLBACK_DEFAULTS["iq_timeout_seconds"]
    )
    SID_NS = _REDACTION_FALLBACK_DEFAULTS["sid_ns"]

    def normalize_bare_jid(jid: str) -> str:
        """Fallback normalizer used only when redaction imports are skipped."""
        if not jid:
            return jid

        # Mimic the minimal bare-JID normalization semantics expected by tests:
        # trim whitespace, drop resources, and normalize case.
        return jid.strip().split("/", 1)[0].lower()

pytestmark = pytest.mark.skipif(
    not REDACTION_TEST_IMPORTS_OK,
    reason="redaction database imports require aiosqlite",
)


TEST_REDACTION_IQ_PROCESSING_DELAY_SECONDS = 0.05


class FakeFrom:
    """Simulate the ``from`` stanza attribute object used in tests."""

    def __init__(self, bare: str):
        self.bare = bare


class FakeMessage:
    """Test double for XMPP message stanzas used by redaction tests.

    The object exposes key message fields (room, nick, body) and optionally
    adds a namespaced stanza-id child to its XML payload to simulate
    server-assigned stable stanza IDs.
    """

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
    """Mock outgoing XMPP IQ stanza used by redaction tests.

    It captures appended XML children and send kwargs, can simulate send
    delays or failures, and tracks in-flight sends for bounded-concurrency
    assertions.
    """

    def __init__(
        self,
        sent,
        *,
        iq_kwargs: dict | None = None,
        delay: float = 0.0,
        fail: bool = False,
        fail_error: str | None = None,
        inflight_tracker: dict[str, int] | None = None,
    ):
        self.sent = sent
        self.children = []
        self.kwargs = dict(iq_kwargs or {})
        self.timeout = None
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


def _build_iq_with_normalized_to(
    sent,
    *,
    delay: float = 0.0,
    inflight_tracker: dict[str, int] | None = None,
    **kwargs,
):
    """Build a fake IQ and expose the recipient through a normalized ``to`` key.

    The real XMPP implementation may pass the target room as ``ito`` or ``mto``
    depending on the helper used. Tests assert the normalized ``to`` value so
    recipient checks stay independent from that implementation detail.
    """
    normalized_kwargs = dict(kwargs)
    if "to" not in normalized_kwargs:
        for recipient_key in ("ito", "mto"):
            if recipient_key in normalized_kwargs:
                normalized_kwargs["to"] = normalized_kwargs[recipient_key]
                break

    return FakeOutgoingIq(
        sent,
        iq_kwargs=normalized_kwargs,
        delay=delay,
        inflight_tracker=inflight_tracker,
    )


def assert_body_contains(message_body: str, *expected_fragments: str) -> None:
    """Assert that all expected fragments are present in a message body."""
    for fragment in expected_fragments:
        assert fragment in message_body


def assert_all_failed_auto_redaction_summary(message_body: str, *, found: int) -> None:
    """Assert common auto-redaction summary text for all-failed requests."""
    assert_body_contains(
        message_body,
        "Auto-redaction completed after ban",
        f"Messages found: {found}",
        "Redacted: 0",
        f"Failed: {found}",
        "Note: The server rejected all redaction requests.",
        "no longer redactable",
        "lacks moderation permissions",
    )


class RedactionBot(DatabaseMixin, RedactionMixin):
    """Test fixture combining database and redaction behavior.

    This lightweight bot captures outbound chat messages and generated
    moderation IQ stanzas so tests can assert redaction behavior. It provides
    mocked message sending, IQ creation, bare-JID normalization, operational
    alerting, and bounded-concurrency tracking.
    """

    @staticmethod
    def redaction_config_defaults() -> dict[str, object]:
        """Return redaction settings used by the fixture."""
        return {
            "redaction_enabled": True,
            "redaction_index_retention_days": 30,
            "redaction_auto_reasons": ["spam", "harassment"],
            "protected_rooms": {"room@conference.example.test"},
            "occupants": {
                "room@conference.example.test": {
                    "Alice": {"jid": "alice@example.org/resource"},
                }
            },
            "command_prefix": "!",
            "redaction_retract_concurrency": 3,
        }

    @staticmethod
    def test_state_default_items() -> dict[str, object]:
        """Return mutable test-only state captured by the fixture."""
        return {
            "sent": [],
            "redaction_stanzas": [],
            "_test_iq_send_delay": 0.0,
            "fail_stanza_ids": set(),
            "fail_stanza_errors": {},
            "redaction_inflight_tracker": {"current": 0, "max": 0},
            "alerts": [],
        }

    def __init__(self):
        for name, value in self.redaction_config_defaults().items():
            setattr(self, name, value)
        for name, value in self.test_state_default_items().items():
            setattr(self, name, value)

    bare_jid = staticmethod(normalize_bare_jid)

    async def bot_send_message(self, **kwargs):
        self.sent.append(kwargs)

    def make_iq_set(self, **kwargs):
        iq = _build_iq_with_normalized_to(
            self.redaction_stanzas,
            delay=self._test_iq_send_delay,
            inflight_tracker=self.redaction_inflight_tracker,
            **kwargs,
        )
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


async def setup_redaction_test_db(bot: RedactionBot, temp_db_path: Path | str) -> None:
    """Initialize the redaction schema using the isolated DB fixture path.

    The shared ``temp_db_path`` fixture monkeypatches both ``config.DB_FILE``
    and ``banbot.db.DB_FILE`` before this helper is called. The assertion keeps
    that relationship explicit so the parameter is not accidental test clutter.
    """
    import config

    expected_db_file = str(temp_db_path)
    configured_db_file = str(config.DB_FILE)
    if expected_db_file != configured_db_file:
        raise ValueError(
            "temp_db_path must match config.DB_FILE before setup_db(); "
            f"got temp_db_path={expected_db_file!r}, "
            f"config.DB_FILE={configured_db_file!r}"
        )

    await bot.setup_db()


@pytest.mark.asyncio
async def test_redaction_indexes_message_with_room_stanza_id(temp_db_path):
    bot = RedactionBot()
    await setup_redaction_test_db(bot, temp_db_path)
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
    await setup_redaction_test_db(bot, temp_db_path)
    try:
        await bot._redaction_index_message(FakeMessage("room@conference.example.test", "Alice", "stanza-1"))
        await bot._redaction_index_message(FakeMessage("room@conference.example.test", "Alice", "stanza-2"))

        await bot.cmd_redact(["alice@example.org", "spam"], "admin@conference.example.test", actor="admin@example.org")

        assert len(bot.redaction_stanzas) == 2
        assert all(stanza.kwargs["to"] == "room@conference.example.test" for stanza in bot.redaction_stanzas)
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
    await setup_redaction_test_db(bot, temp_db_path)
    try:
        await bot.cmd_redact(
            ["id", "room@conference.example.test", "stanza-1", "spam"],
            "admin@conference.example.test",
            actor="admin@example.org",
        )

        assert len(bot.redaction_stanzas) == 1
        iq = bot.redaction_stanzas[0]
        assert iq.kwargs["to"] == "room@conference.example.test"
        assert iq.timeout == REDACTION_IQ_TIMEOUT_SECONDS
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
    await setup_redaction_test_db(bot, temp_db_path)
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
async def test_automatic_redaction_cleanup_deletes_old_entries_without_message(temp_db_path):
    bot = RedactionBot()
    bot.redaction_index_retention_days = 30
    await setup_redaction_test_db(bot, temp_db_path)
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

        result = await bot.run_redaction_cleanup_automatic(actor="system")

        assert result["deleted"] == 1
        assert bot.sent == []
        async with bot.db.execute("SELECT COUNT(*) FROM redaction_index") as cursor:
            row = await cursor.fetchone()
        assert row[0] == 0
    finally:
        await bot.db.close()


@pytest.mark.asyncio
async def test_redaction_cleanup_worker_runs_after_daily_interval(monkeypatch):
    bot = RedactionBot()
    sleeps = []
    cleanup_calls = []

    async def fake_sleep(delay):
        sleeps.append(delay)

    async def fake_cleanup(actor="system"):
        cleanup_calls.append(actor)
        raise asyncio.CancelledError()

    monkeypatch.setattr("banbot.redaction.asyncio.sleep", fake_sleep)
    bot.run_redaction_cleanup_automatic = fake_cleanup

    with pytest.raises(asyncio.CancelledError):
        await bot.redaction_cleanup_worker()

    assert sleeps == [REDACTION_CLEANUP_INTERVAL_SECONDS]
    assert cleanup_calls == ["system"]


@pytest.mark.asyncio
async def test_auto_redaction_runs_for_matching_ban_reason(temp_db_path):
    bot = RedactionBot()
    await setup_redaction_test_db(bot, temp_db_path)
    try:
        await bot._redaction_index_message(FakeMessage("room@conference.example.test", "Alice", "stanza-1"))

        await bot.maybe_auto_redact_after_ban("alice@example.org", "confirmed spam", actor="admin@example.org")

        assert len(bot.redaction_stanzas) == 1
        assert "Auto-redaction completed after ban" in bot.sent[-1]["mbody"]
    finally:
        await bot.db.close()


@pytest.mark.asyncio
async def test_auto_redaction_reports_previously_redacted_when_no_candidates(temp_db_path):
    bot = RedactionBot()
    await setup_redaction_test_db(bot, temp_db_path)
    try:
        await bot._redaction_index_message(FakeMessage("room@conference.example.test", "Alice", "stanza-1"))
        await bot._redaction_index_message(FakeMessage("room@conference.example.test", "Alice", "stanza-2"))

        await bot.maybe_auto_redact_after_ban("alice@example.org", "confirmed spam", actor="admin@example.org")
        assert len(bot.redaction_stanzas) == 2

        await bot.maybe_auto_redact_after_ban("alice@example.org", "confirmed spam", actor="admin@example.org")

        body = bot.sent[-1]["mbody"]
        assert "Auto-redaction completed after ban" in body
        assert "No redactable indexed stanza IDs found for this JID." in body
        assert "Previously redacted messages: 2" in body
        assert "and not already redacted can be redacted" in body
        assert "No indexed stanza IDs found for this JID." not in body
        assert len(bot.redaction_stanzas) == 2
    finally:
        await bot.db.close()


# Intentionally synchronous: this test only exercises pure string-matching
# logic and does not call async redaction/database code.
def test_auto_reason_matching_is_case_insensitive():
    bot = RedactionBot()

    assert bot._redaction_auto_reason_matches("Confirmed SPAM wave") == "spam"
    assert bot._redaction_auto_reason_matches("ordinary moderation note") is None


@pytest.mark.asyncio
async def test_redact_rows_uses_bounded_concurrency_and_batch_marks_rows(temp_db_path):
    bot = RedactionBot()
    bot.redaction_retract_concurrency = 2
    bot._test_iq_send_delay = TEST_REDACTION_IQ_PROCESSING_DELAY_SECONDS
    await setup_redaction_test_db(bot, temp_db_path)
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
    await setup_redaction_test_db(bot, temp_db_path)
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
    await setup_redaction_test_db(bot, temp_db_path)
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


@pytest.mark.asyncio
async def test_auto_redaction_suppresses_failure_alerts_and_adds_all_failed_note(temp_db_path):
    bot = RedactionBot()
    bot.fail_stanza_ids = {"stanza-1", "stanza-2"}
    bot.fail_stanza_errors = {
        "stanza-1": '<iq type="error"><moderate id="stanza-1" /></iq>',
        "stanza-2": '<iq type="error"><moderate id="stanza-2" /></iq>',
    }
    await setup_redaction_test_db(bot, temp_db_path)
    try:
        await bot._redaction_index_message(FakeMessage("room@conference.example.test", "Alice", "stanza-1"))
        await bot._redaction_index_message(FakeMessage("room@conference.example.test", "Alice", "stanza-2"))

        await bot.maybe_auto_redact_after_ban("alice@example.org", "confirmed spam", actor="admin@example.org")

        body = bot.sent[-1]["mbody"]
        assert_all_failed_auto_redaction_summary(body, found=2)
        assert bot.alerts == []
    finally:
        await bot.db.close()
