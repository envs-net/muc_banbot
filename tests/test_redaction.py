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

    def normalize_bare_jid(jid: str | None) -> str:
        """Fallback normalizer used only when redaction imports are skipped."""
        if jid is None:
            return ""

        # Mimic the minimal bare-JID normalization semantics expected by tests:
        # trim whitespace, drop resources, and normalize case.
        return jid.strip().split("/", 1)[0].lower()

pytestmark = pytest.mark.skipif(
    not REDACTION_TEST_IMPORTS_OK,
    reason="redaction database imports require aiosqlite",
)


TEST_IQ_SEND_DELAY_SECONDS = 0.05
DEFAULT_TEST_MESSAGE_BODY = "test message"
TEST_ROOM_JID = "room@conference.example.test"
TEST_ADMIN_ROOM_JID = "admin@conference.example.test"
TEST_ACTOR_JID = "admin@example.org"
TEST_SENDER_NICK = "Alice"
TEST_SENDER_JID = "alice@example.org"
TEST_SENDER_RESOURCE_JID = "alice@example.org/resource"
TEST_REDACTION_REASON = "spam"
TEST_MATCHING_BAN_REASON = "confirmed spam"
TEST_NON_MATCHING_REASON = "ordinary moderation note"
TEST_UPPERCASE_MATCHING_REASON = "Confirmed SPAM wave"
TEST_STANZA_1 = "stanza-1"
TEST_STANZA_2 = "stanza-2"
TEST_OLD_STANZA = "old-stanza"
TEST_SERVER_REJECTED_IQ_ERROR = '<iq type="error"><moderate id="{stanza_id}" /></iq>'


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

    def __init__(
        self,
        room: str,
        nick: str,
        stanza_id: str | None = None,
        body: str = DEFAULT_TEST_MESSAGE_BODY,
    ):
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
        fail_stanza_ids: set[str] | None = None,
        fail_stanza_errors: dict[str, str] | None = None,
        inflight_tracker: dict[str, int] | None = None,
    ):
        self.sent = sent
        self.children = []
        self.kwargs = dict(iq_kwargs or {})
        self.timeout = None
        self.delay = delay
        self.fail = fail
        self.fail_error = fail_error or "simulated retract failure"
        self.fail_stanza_ids = fail_stanza_ids or set()
        self.fail_stanza_errors = fail_stanza_errors or {}
        self.inflight_tracker = inflight_tracker

    def append(self, element):
        self.children.append(element)
        stanza_id = element.attrib.get("id")
        if stanza_id in self.fail_stanza_ids:
            self.fail = True
            self.fail_error = self.fail_stanza_errors.get(stanza_id, self.fail_error)

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
    fail_stanza_ids: set[str] | None = None,
    fail_stanza_errors: dict[str, str] | None = None,
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
        fail_stanza_ids=fail_stanza_ids,
        fail_stanza_errors=fail_stanza_errors,
        inflight_tracker=inflight_tracker,
    )


def latest_message_body(bot: "RedactionBot") -> str:
    """Return the most recent message body sent by the test bot."""
    return bot.sent[-1]["mbody"]


def assert_body_contains(message_body: str, expected_fragment: str) -> None:
    """Assert that one expected fragment is present in a message body."""
    assert expected_fragment in message_body


def assert_body_lacks(message_body: str, unexpected_fragment: str) -> None:
    """Assert that one unexpected fragment is absent from a message body."""
    assert unexpected_fragment not in message_body


def assert_summary_count(message_body: str, label: str, count: int) -> None:
    """Assert one numeric redaction summary line."""
    assert_body_contains(message_body, f"{label}: {count}")


def assert_auto_redaction_summary_header(message_body: str) -> None:
    """Assert the common auto-redaction summary header."""
    assert_body_contains(message_body, "Auto-redaction completed after ban")


def assert_server_rejected_note(message_body: str) -> None:
    """Assert the explanatory note for fully rejected auto-redactions."""
    assert_body_contains(message_body, "Note: The server rejected all redaction requests.")
    assert_body_contains(message_body, "no longer redactable")
    assert_body_contains(message_body, "lacks moderation permissions")


def assert_all_failed_auto_redaction_summary(message_body: str, *, found: int) -> None:
    """Assert common auto-redaction summary text for all-failed requests."""
    assert_auto_redaction_summary_header(message_body)
    assert_summary_count(message_body, "Messages found", found)
    assert_summary_count(message_body, "Redacted", 0)
    assert_summary_count(message_body, "Failed", found)
    assert_server_rejected_note(message_body)


def assert_previously_redacted_summary(message_body: str, *, count: int) -> None:
    """Assert the summary shown when only already-redacted rows remain."""
    assert_auto_redaction_summary_header(message_body)
    assert_body_contains(message_body, "No redactable indexed stanza IDs found for this JID.")
    assert_body_contains(message_body, f"Previously redacted messages: {count}")
    assert_body_contains(message_body, "and not already redacted can be redacted")
    assert_body_lacks(message_body, "No indexed stanza IDs found for this JID.")


def moderation_element(iq: FakeOutgoingIq) -> ET.Element:
    """Return the single moderation element from a fake IQ."""
    assert len(iq.children) == 1
    return iq.children[0]


def assert_moderation_identity(element: ET.Element, stanza_id: str) -> None:
    """Assert the moderation element targets the expected stanza."""
    assert element.tag == "{urn:xmpp:message-moderate:1}moderate"
    assert element.attrib["id"] == stanza_id


def assert_moderation_has_retract(element: ET.Element) -> None:
    """Assert the moderation element contains a retract child."""
    assert element.find("{urn:xmpp:message-retract:1}retract") is not None


def assert_moderation_reason(element: ET.Element, reason: str) -> None:
    """Assert the moderation element contains the expected reason text."""
    reason_node = element.find("{urn:xmpp:message-moderate:1}reason")
    assert reason_node is not None
    assert reason_node.text == reason


def assert_moderation_request(iq: FakeOutgoingIq, *, stanza_id: str, reason: str) -> None:
    """Assert that a fake IQ contains the expected moderation retract stanza."""
    element = moderation_element(iq)
    assert_moderation_identity(element, stanza_id)
    assert_moderation_has_retract(element)
    assert_moderation_reason(element, reason)


async def redaction_index_count(bot: "RedactionBot") -> int:
    """Return the number of redaction index rows."""
    async with bot.db.execute("SELECT COUNT(*) FROM redaction_index") as cursor:
        row = await cursor.fetchone()
    return int(row[0])


async def redacted_index_count(bot: "RedactionBot") -> int:
    """Return the number of redaction index rows marked redacted."""
    async with bot.db.execute(
        "SELECT COUNT(*) FROM redaction_index WHERE redacted_at IS NOT NULL"
    ) as cursor:
        row = await cursor.fetchone()
    return int(row[0])


async def first_redacted_at(bot: "RedactionBot") -> int | None:
    """Return the first redacted_at value from the redaction index."""
    async with bot.db.execute("SELECT redacted_at FROM redaction_index") as cursor:
        row = await cursor.fetchone()
    return row[0]


async def redacted_stanza_ids(bot: "RedactionBot") -> list[tuple[str]]:
    """Return stanza IDs for rows marked redacted."""
    async with bot.db.execute(
        "SELECT stanza_id FROM redaction_index WHERE redacted_at IS NOT NULL"
    ) as cursor:
        return await cursor.fetchall()


async def insert_old_redaction_entry(bot: "RedactionBot") -> None:
    """Insert one expired redaction index row for cleanup tests."""
    await bot.db.execute(
        """
        INSERT INTO redaction_index (room_jid, sender_jid, stanza_id, created_at)
        VALUES (?, ?, ?, ?)
        """,
        (
            TEST_ROOM_JID,
            TEST_SENDER_JID,
            TEST_OLD_STANZA,
            int(time.time()) - 31 * 86400,
        ),
    )
    await bot.db.commit()


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
            "redaction_auto_reasons": [TEST_REDACTION_REASON, "harassment"],
            "protected_rooms": {TEST_ROOM_JID},
            "occupants": {
                TEST_ROOM_JID: {
                    TEST_SENDER_NICK: {"jid": TEST_SENDER_RESOURCE_JID},
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
            "audit_events": [],
        }

    def __init__(self):
        for name, value in self.redaction_config_defaults().items():
            setattr(self, name, value)
        for name, value in self.test_state_default_items().items():
            setattr(self, name, value)

    @staticmethod
    def bare_jid(jid: str | None) -> str:
        return normalize_bare_jid(jid)

    async def bot_send_message(self, **kwargs):
        self.sent.append(kwargs)

    def make_iq_set(self, **kwargs):
        return _build_iq_with_normalized_to(
            self.redaction_stanzas,
            delay=self._test_iq_send_delay,
            inflight_tracker=self.redaction_inflight_tracker,
            fail_stanza_ids=self.fail_stanza_ids,
            fail_stanza_errors=self.fail_stanza_errors,
            **kwargs,
        )

    async def send_operational_alert(self, key, title, message, enabled=True, details=None):
        if enabled:
            self.alerts.append((key, title, message, details or {}))

    async def audit_event(self, event_type: str, **kwargs):
        self.audit_events.append((event_type, kwargs))


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
        msg = FakeMessage(TEST_ROOM_JID, TEST_SENDER_NICK, TEST_STANZA_1)

        indexed = await bot._redaction_index_message(msg)

        assert indexed is True
        async with bot.db.execute("SELECT room_jid, sender_jid, stanza_id FROM redaction_index") as cursor:
            rows = await cursor.fetchall()
        assert rows == [(TEST_ROOM_JID, TEST_SENDER_JID, TEST_STANZA_1)]
    finally:
        await bot.db.close()


@pytest.mark.asyncio
async def test_redact_jid_retracts_all_indexed_messages(temp_db_path):
    bot = RedactionBot()
    await setup_redaction_test_db(bot, temp_db_path)
    try:
        await bot._redaction_index_message(FakeMessage(TEST_ROOM_JID, TEST_SENDER_NICK, TEST_STANZA_1))
        await bot._redaction_index_message(FakeMessage(TEST_ROOM_JID, TEST_SENDER_NICK, TEST_STANZA_2))

        await bot.cmd_redact([TEST_SENDER_JID, TEST_REDACTION_REASON], TEST_ADMIN_ROOM_JID, actor=TEST_ACTOR_JID)

        assert len(bot.redaction_stanzas) == 2
        assert all(stanza.kwargs["to"] == TEST_ROOM_JID for stanza in bot.redaction_stanzas)
        assert_moderation_request(
            bot.redaction_stanzas[0],
            stanza_id=TEST_STANZA_1,
            reason=TEST_REDACTION_REASON,
        )
        assert_moderation_request(
            bot.redaction_stanzas[1],
            stanza_id=TEST_STANZA_2,
            reason=TEST_REDACTION_REASON,
        )
        assert_summary_count(latest_message_body(bot), "Messages found", 2)
        assert_summary_count(latest_message_body(bot), "Redacted", 2)
        assert await redacted_index_count(bot) == 2
    finally:
        await bot.db.close()


@pytest.mark.asyncio
async def test_redact_id_retracts_single_stanza(temp_db_path):
    bot = RedactionBot()
    await setup_redaction_test_db(bot, temp_db_path)
    try:
        await bot.cmd_redact(
            ["id", TEST_ROOM_JID, TEST_STANZA_1, TEST_REDACTION_REASON],
            TEST_ADMIN_ROOM_JID,
            actor=TEST_ACTOR_JID,
        )

        assert len(bot.redaction_stanzas) == 1
        iq = bot.redaction_stanzas[0]
        assert iq.kwargs["to"] == TEST_ROOM_JID
        assert iq.timeout == REDACTION_IQ_TIMEOUT_SECONDS
        assert_moderation_request(iq, stanza_id=TEST_STANZA_1, reason=TEST_REDACTION_REASON)
        assert_body_contains(latest_message_body(bot), f"Stanza ID: {TEST_STANZA_1}")
    finally:
        await bot.db.close()


@pytest.mark.asyncio
async def test_redact_cleanup_deletes_old_entries(temp_db_path):
    bot = RedactionBot()
    bot.redaction_index_retention_days = 30
    await setup_redaction_test_db(bot, temp_db_path)
    try:
        await insert_old_redaction_entry(bot)

        await bot.cmd_redact(["cleanup"], TEST_ADMIN_ROOM_JID, actor=TEST_ACTOR_JID)

        assert_summary_count(latest_message_body(bot), "Deleted entries", 1)
        assert await redaction_index_count(bot) == 0
    finally:
        await bot.db.close()


@pytest.mark.asyncio
async def test_automatic_redaction_cleanup_deletes_old_entries_without_message(temp_db_path):
    bot = RedactionBot()
    bot.redaction_index_retention_days = 30
    await setup_redaction_test_db(bot, temp_db_path)
    try:
        await insert_old_redaction_entry(bot)

        result = await bot.run_redaction_cleanup_automatic(actor="system")

        assert result["deleted"] == 1
        assert bot.sent == []
        assert await redaction_index_count(bot) == 0
    finally:
        await bot.db.close()


@pytest.mark.asyncio
async def test_automatic_redaction_cleanup_skips_noop_audit_when_retention_disabled(temp_db_path):
    bot = RedactionBot()
    bot.redaction_index_retention_days = 0
    await setup_redaction_test_db(bot, temp_db_path)
    try:
        result = await bot.run_redaction_cleanup_automatic(actor="system")

        assert result["deleted"] == 0
        assert result["skipped_reason"] == "retention disabled"
        assert bot.sent == []
        assert bot.audit_events == []
    finally:
        await bot.db.close()


@pytest.mark.asyncio
async def test_automatic_redaction_cleanup_skips_noop_audit_when_nothing_expired(temp_db_path):
    bot = RedactionBot()
    bot.redaction_index_retention_days = 30
    await setup_redaction_test_db(bot, temp_db_path)
    try:
        result = await bot.run_redaction_cleanup_automatic(actor="system")

        assert result["deleted"] == 0
        assert bot.sent == []
        assert bot.audit_events == []
    finally:
        await bot.db.close()


@pytest.mark.asyncio
async def test_manual_redaction_cleanup_keeps_noop_audit_when_retention_disabled(temp_db_path):
    bot = RedactionBot()
    bot.redaction_index_retention_days = 0
    await setup_redaction_test_db(bot, temp_db_path)
    try:
        await bot.redact_cleanup(TEST_ADMIN_ROOM_JID, actor=TEST_ACTOR_JID)

        assert "Retention: keep forever" in latest_message_body(bot)
        assert bot.audit_events == [
            (
                "redact_cleanup",
                {
                    "actor": TEST_ACTOR_JID,
                    "room": None,
                    "target_type": "redaction_index",
                    "target": "cleanup",
                    "jid": None,
                    "comment": "retention disabled; keep forever",
                    "details": {"retention_days": 0, "deleted": 0},
                },
            )
        ]
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
async def test_redaction_cleanup_worker_continues_after_successful_iterations(monkeypatch):
    bot = RedactionBot()
    sleeps = []
    cleanup_calls = []

    async def fake_sleep(delay):
        sleeps.append(delay)

    async def fake_cleanup(actor="system"):
        cleanup_calls.append(actor)
        if len(cleanup_calls) >= 3:
            raise asyncio.CancelledError()
        return {"deleted": 0}

    monkeypatch.setattr("banbot.redaction.asyncio.sleep", fake_sleep)
    bot.run_redaction_cleanup_automatic = fake_cleanup

    with pytest.raises(asyncio.CancelledError):
        await bot.redaction_cleanup_worker()

    assert sleeps == [
        REDACTION_CLEANUP_INTERVAL_SECONDS,
        REDACTION_CLEANUP_INTERVAL_SECONDS,
        REDACTION_CLEANUP_INTERVAL_SECONDS,
    ]
    assert cleanup_calls == ["system", "system", "system"]


@pytest.mark.asyncio
async def test_auto_redaction_runs_for_matching_ban_reason(temp_db_path):
    bot = RedactionBot()
    await setup_redaction_test_db(bot, temp_db_path)
    try:
        await bot._redaction_index_message(FakeMessage(TEST_ROOM_JID, TEST_SENDER_NICK, TEST_STANZA_1))

        await bot.maybe_auto_redact_after_ban(TEST_SENDER_JID, TEST_MATCHING_BAN_REASON, actor=TEST_ACTOR_JID)

        assert len(bot.redaction_stanzas) == 1
        assert_auto_redaction_summary_header(latest_message_body(bot))
        assert await first_redacted_at(bot) is not None
    finally:
        await bot.db.close()


@pytest.mark.asyncio
async def test_auto_redaction_reports_previously_redacted_when_no_candidates(temp_db_path):
    bot = RedactionBot()
    await setup_redaction_test_db(bot, temp_db_path)
    try:
        await bot._redaction_index_message(FakeMessage(TEST_ROOM_JID, TEST_SENDER_NICK, TEST_STANZA_1))
        await bot._redaction_index_message(FakeMessage(TEST_ROOM_JID, TEST_SENDER_NICK, TEST_STANZA_2))

        await bot.maybe_auto_redact_after_ban(TEST_SENDER_JID, TEST_MATCHING_BAN_REASON, actor=TEST_ACTOR_JID)
        assert len(bot.redaction_stanzas) == 2

        await bot.maybe_auto_redact_after_ban(TEST_SENDER_JID, TEST_MATCHING_BAN_REASON, actor=TEST_ACTOR_JID)

        assert_previously_redacted_summary(latest_message_body(bot), count=2)
        assert len(bot.redaction_stanzas) == 2
    finally:
        await bot.db.close()


# Intentionally synchronous: this test only exercises pure string-matching
# logic and does not call async redaction/database code.
def test_auto_reason_matching_is_case_insensitive():
    bot = RedactionBot()

    assert bot._redaction_auto_reason_matches(TEST_UPPERCASE_MATCHING_REASON) == TEST_REDACTION_REASON
    assert bot._redaction_auto_reason_matches(TEST_NON_MATCHING_REASON) is None


@pytest.mark.asyncio
async def test_redact_rows_uses_bounded_concurrency_and_batch_marks_rows(temp_db_path):
    bot = RedactionBot()
    bot.redaction_retract_concurrency = 2
    bot._test_iq_send_delay = TEST_IQ_SEND_DELAY_SECONDS * 4
    await setup_redaction_test_db(bot, temp_db_path)
    try:
        for i in range(8):
            await bot._redaction_index_message(
                FakeMessage(TEST_ROOM_JID, TEST_SENDER_NICK, f"stanza-{i}")
            )

        summary = await bot.redact_jid_messages(
            TEST_SENDER_JID,
            reason=TEST_REDACTION_REASON,
            actor=TEST_ACTOR_JID,
            announce=False,
        )

        assert summary["found"] == 8
        assert summary["redacted"] == 8
        assert summary["failed"] == 0
        assert len(bot.redaction_stanzas) == 8
        assert bot.redaction_inflight_tracker["max"] == 2

        assert await redacted_index_count(bot) == 8
    finally:
        await bot.db.close()


@pytest.mark.asyncio
async def test_redact_rows_counts_failed_retractions_without_marking_rows(temp_db_path):
    bot = RedactionBot()
    bot.fail_stanza_ids = {TEST_STANZA_2}
    await setup_redaction_test_db(bot, temp_db_path)
    try:
        await bot._redaction_index_message(FakeMessage(TEST_ROOM_JID, TEST_SENDER_NICK, TEST_STANZA_1))
        await bot._redaction_index_message(FakeMessage(TEST_ROOM_JID, TEST_SENDER_NICK, TEST_STANZA_2))

        summary = await bot.redact_jid_messages(
            TEST_SENDER_JID,
            reason=TEST_REDACTION_REASON,
            actor=TEST_ACTOR_JID,
            announce=False,
        )

        assert summary["found"] == 2
        assert summary["redacted"] == 1
        assert summary["failed"] == 1

        assert await redacted_stanza_ids(bot) == [(TEST_STANZA_1,)]
    finally:
        await bot.db.close()


@pytest.mark.asyncio
async def test_redact_rows_treats_already_retracted_stanzas_as_skipped(temp_db_path):
    bot = RedactionBot()
    bot.fail_stanza_ids = {TEST_STANZA_2}
    bot.fail_stanza_errors = {TEST_STANZA_2: "item-not-found: stanza already retracted"}
    await setup_redaction_test_db(bot, temp_db_path)
    try:
        await bot._redaction_index_message(FakeMessage(TEST_ROOM_JID, TEST_SENDER_NICK, TEST_STANZA_1))
        await bot._redaction_index_message(FakeMessage(TEST_ROOM_JID, TEST_SENDER_NICK, TEST_STANZA_2))

        summary = await bot.redact_jid_messages(
            TEST_SENDER_JID,
            reason=TEST_REDACTION_REASON,
            actor=TEST_ACTOR_JID,
            announce=False,
        )

        assert summary["found"] == 2
        assert summary["redacted"] == 1
        assert summary["skipped"] == 1
        assert summary["failed"] == 0
        assert bot.alerts == []

        assert await redacted_index_count(bot) == 2
    finally:
        await bot.db.close()


@pytest.mark.asyncio
async def test_auto_redaction_suppresses_failure_alerts_and_adds_all_failed_note(temp_db_path):
    bot = RedactionBot()
    bot.fail_stanza_ids = {TEST_STANZA_1, TEST_STANZA_2}
    bot.fail_stanza_errors = {
        TEST_STANZA_1: TEST_SERVER_REJECTED_IQ_ERROR.format(stanza_id=TEST_STANZA_1),
        TEST_STANZA_2: TEST_SERVER_REJECTED_IQ_ERROR.format(stanza_id=TEST_STANZA_2),
    }
    await setup_redaction_test_db(bot, temp_db_path)
    try:
        await bot._redaction_index_message(FakeMessage(TEST_ROOM_JID, TEST_SENDER_NICK, TEST_STANZA_1))
        await bot._redaction_index_message(FakeMessage(TEST_ROOM_JID, TEST_SENDER_NICK, TEST_STANZA_2))

        await bot.maybe_auto_redact_after_ban(TEST_SENDER_JID, TEST_MATCHING_BAN_REASON, actor=TEST_ACTOR_JID)

        assert_all_failed_auto_redaction_summary(latest_message_body(bot), found=2)
        assert bot.alerts == []
    finally:
        await bot.db.close()
