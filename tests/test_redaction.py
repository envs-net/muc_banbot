"""Redaction index and command tests."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

REDACTION_TEST_IMPORTS_OK = True

# Keep fallback defaults synchronized with banbot.redaction constants.
# They are used only if imports fail, and also asserted against imported
# constants when the real module is available.
_REDACTION_FALLBACK_DEFAULTS = {
    "cleanup_interval_seconds": 24 * 60 * 60,
    "iq_timeout_seconds": 5,
    "sid_ns": "urn:xmpp:sid:0",
}

try:
    from banbot.db import DatabaseMixin
    from banbot.redaction import (
        FASTEN_NS,
        LEGACY_MODERATE_NS,
        LEGACY_RETRACT_NS,
        MAM_NS,
        MODERATE_NS,
        REDACTION_CLEANUP_INTERVAL_SECONDS,
        REDACTION_IQ_TIMEOUT_SECONDS,
        RETRACT_NS,
        RedactionMixin,
        SID_NS,
        XDATA_NS,
        _redaction_exception_summary,
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

    REDACTION_CLEANUP_INTERVAL_SECONDS = (
        _REDACTION_FALLBACK_DEFAULTS["cleanup_interval_seconds"]
    )
    REDACTION_IQ_TIMEOUT_SECONDS = (
        _REDACTION_FALLBACK_DEFAULTS["iq_timeout_seconds"]
    )
    SID_NS = _REDACTION_FALLBACK_DEFAULTS["sid_ns"]

    def normalize_bare_jid(jid: str | None) -> str:
        """Fallback-only mirror of ``banbot.utils.bare_jid``.

        This helper is used only when optional imports are unavailable in the
        test environment. It intentionally implements only the subset relied on
        by these tests:

        * ``None`` -> ``""``
        * remove any resource part after the first ``/``

        It does *not* attempt broader JID normalization or validation
        semantics. If ``banbot.utils.bare_jid`` changes for these two rules,
        update this fallback to keep tests aligned.
        """
        if jid is None:
            return ""
        return jid.split("/", 1)[0]

pytestmark = pytest.mark.skipif(
    not REDACTION_TEST_IMPORTS_OK,
    reason="redaction database imports require aiosqlite",
)


# Small non-zero delay used by concurrency tests to force overlap between tasks
# without making the suite slow/flaky; some assertions derive total wait via "* 4".
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

@pytest.mark.skipif(
    not REDACTION_TEST_IMPORTS_OK,
    reason="Requires successful banbot.redaction imports to validate module constants.",
)
def test_redaction_fallback_defaults_match_module_constants() -> None:
    """Ensure fallback constants remain aligned with banbot.redaction values."""
    assert (
        _REDACTION_FALLBACK_DEFAULTS["cleanup_interval_seconds"]
        == REDACTION_CLEANUP_INTERVAL_SECONDS
    )
    assert _REDACTION_FALLBACK_DEFAULTS["iq_timeout_seconds"] == REDACTION_IQ_TIMEOUT_SECONDS
    assert _REDACTION_FALLBACK_DEFAULTS["sid_ns"] == SID_NS


def test_redaction_timeout_summary_is_not_reported_as_server_rejection() -> None:
    assert _redaction_exception_summary(asyncio.TimeoutError()) == "redaction request timed out"


@pytest.mark.skipif(
    REDACTION_TEST_IMPORTS_OK,
    reason="Only relevant when optional redaction imports fail.",
)
def test_redaction_fallback_constants_and_helper_when_imports_fail() -> None:
    """Validate fallback constants/helper behavior when optional imports fail."""
    if REDACTION_TEST_IMPORTS_OK:
        return

    assert (
        REDACTION_CLEANUP_INTERVAL_SECONDS
        == _REDACTION_FALLBACK_DEFAULTS["cleanup_interval_seconds"]
    )
    assert (
        REDACTION_IQ_TIMEOUT_SECONDS
        == _REDACTION_FALLBACK_DEFAULTS["iq_timeout_seconds"]
    )
    assert SID_NS == _REDACTION_FALLBACK_DEFAULTS["sid_ns"]

    assert normalize_bare_jid(None) == ""
    assert normalize_bare_jid("alice@example.org/resource") == "alice@example.org"
    assert normalize_bare_jid("alice@example.org") == "alice@example.org"


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


def fake_moderation_confirmation(
    room: str,
    stanza_id: str,
    *,
    legacy: bool = False,
) -> FakeMessage:
    """Build a live XEP-0425 moderation announcement for one stanza ID."""
    msg = FakeMessage(room, "", body="")
    if legacy:
        apply_to = ET.SubElement(msg.xml, f"{{{FASTEN_NS}}}apply-to", {"id": stanza_id})
        moderated = ET.SubElement(apply_to, f"{{{LEGACY_MODERATE_NS}}}moderated")
        ET.SubElement(moderated, f"{{{LEGACY_RETRACT_NS}}}retract")
    else:
        retract = ET.SubElement(msg.xml, f"{{{RETRACT_NS}}}retract", {"id": stanza_id})
        ET.SubElement(retract, f"{{{MODERATE_NS}}}moderated")
    return msg


def fake_mixed_prosody_confirmation(room: str, stanza_id: str) -> FakeMessage:
    """Build a mixed-version moderation broadcast seen in deployed modules."""
    msg = FakeMessage(room, "", body="")
    apply_to = ET.SubElement(msg.xml, f"{{{FASTEN_NS}}}apply-to", {"id": stanza_id})
    retract = ET.SubElement(apply_to, f"{{{RETRACT_NS}}}retract")
    ET.SubElement(retract, f"{{{MODERATE_NS}}}moderated")
    return msg


def fake_mam_tombstone(
    room: str,
    stanza_id: str,
    *,
    result_id: str | None = None,
) -> FakeMessage:
    """Build one archived XEP-0425 tombstone inside a MAM result."""
    msg = FakeMessage(room, "", body="")
    result = ET.SubElement(
        msg.xml,
        f"{{{MAM_NS}}}result",
        {"id": result_id or stanza_id, "queryid": "mam-query"},
    )
    forwarded = ET.SubElement(result, "{urn:xmpp:forward:0}forwarded")
    archived = ET.SubElement(forwarded, "{jabber:client}message")
    ET.SubElement(
        archived,
        f"{{{SID_NS}}}stanza-id",
        {"by": room, "id": stanza_id},
    )
    retracted = ET.SubElement(
        archived,
        f"{{{RETRACT_NS}}}retracted",
        {"id": stanza_id},
    )
    ET.SubElement(retracted, f"{{{MODERATE_NS}}}moderated")
    return msg


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


class FutureReturningOutgoingIq(FakeOutgoingIq):
    """Mirror Slixmpp by returning a Future directly from ``send()``."""

    def send(self, timeout=None):
        async def run_send():
            return await super(FutureReturningOutgoingIq, self).send(timeout=timeout)

        return asyncio.ensure_future(run_send())


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
    if not bot.sent:
        raise AssertionError(
            "latest_message_body() called before any message was sent"
        )
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
            "_test_iq_returns_future": False,
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

    bare_jid = staticmethod(normalize_bare_jid)

    async def bot_send_message(self, **kwargs):
        self.sent.append(kwargs)

    def make_iq_set(self, **kwargs):
        iq = _build_iq_with_normalized_to(
            self.redaction_stanzas,
            delay=self._test_iq_send_delay,
            inflight_tracker=self.redaction_inflight_tracker,
            fail_stanza_ids=self.fail_stanza_ids,
            fail_stanza_errors=self.fail_stanza_errors,
            **kwargs,
        )
        if not self._test_iq_returns_future:
            return iq
        return FutureReturningOutgoingIq(
            self.redaction_stanzas,
            iq_kwargs=iq.kwargs,
            delay=self._test_iq_send_delay,
            fail_stanza_ids=self.fail_stanza_ids,
            fail_stanza_errors=self.fail_stanza_errors,
            inflight_tracker=self.redaction_inflight_tracker,
        )

    async def send_operational_alert(self, key, title, message, enabled=True, details=None):
        if enabled:
            self.alerts.append((key, title, message, details or {}))

    async def audit_event(self, event_type: str, **kwargs):
        self.audit_events.append((event_type, kwargs))


def test_redaction_confirmation_ids_support_current_and_legacy_xep_formats() -> None:
    current = fake_moderation_confirmation(TEST_ROOM_JID, TEST_STANZA_1)
    legacy = fake_moderation_confirmation(TEST_ROOM_JID, TEST_STANZA_2, legacy=True)
    mixed = fake_mixed_prosody_confirmation(TEST_ROOM_JID, "mixed-stanza")

    assert RedactionMixin._redaction_confirmation_ids(current) == {TEST_STANZA_1}
    assert RedactionMixin._redaction_confirmation_ids(legacy) == {TEST_STANZA_2}
    assert RedactionMixin._redaction_confirmation_ids(mixed) == {"mixed-stanza"}


def test_mam_ids_query_builds_extended_archive_filter() -> None:
    query = RedactionMixin._redaction_build_mam_ids_query(
        "query-1",
        [TEST_STANZA_1, TEST_STANZA_2],
    )

    assert query.tag == f"{{{MAM_NS}}}query"
    assert query.attrib == {"queryid": "query-1"}
    form = query.find(f"{{{XDATA_NS}}}x")
    assert form is not None and form.attrib["type"] == "submit"

    fields = {
        field.attrib["var"]: [
            value.text for value in field.findall(f"{{{XDATA_NS}}}value")
        ]
        for field in form.findall(f"{{{XDATA_NS}}}field")
    }
    assert fields == {
        "FORM_TYPE": [MAM_NS],
        "ids": [TEST_STANZA_1, TEST_STANZA_2],
    }


def test_mam_tombstone_parser_accepts_result_and_stanza_ids() -> None:
    tombstone = fake_mam_tombstone(
        TEST_ROOM_JID,
        TEST_STANZA_1,
        result_id="different-result-id",
    )

    assert RedactionMixin._redaction_mam_tombstone_ids(
        [tombstone],
        {TEST_STANZA_1, TEST_STANZA_2},
    ) == {TEST_STANZA_1}


def test_mam_tombstone_parser_rejects_unmodified_archive_messages() -> None:
    message = FakeMessage(TEST_ROOM_JID, "", body="")
    result = ET.SubElement(
        message.xml,
        f"{{{MAM_NS}}}result",
        {"id": TEST_STANZA_1},
    )
    forwarded = ET.SubElement(result, "{urn:xmpp:forward:0}forwarded")
    ET.SubElement(forwarded, "{jabber:client}message")

    assert RedactionMixin._redaction_mam_tombstone_ids(
        [message],
        {TEST_STANZA_1},
    ) == set()


@pytest.mark.asyncio
async def test_mam_verification_falls_back_to_indexed_time_window() -> None:
    bot = RedactionBot()
    timestamp = int(time.time())
    exact_queries = []
    window_queries = []

    async def no_exact_match(room_jid, stanza_ids):
        exact_queries.append((room_jid, list(stanza_ids)))
        return [], RuntimeError("extended ids unsupported")

    async def matching_window(room_jid, start_ts, end_ts):
        window_queries.append((room_jid, start_ts, end_ts))
        return [fake_mam_tombstone(room_jid, TEST_STANZA_1)]

    bot._redaction_query_mam_ids = no_exact_match
    bot._redaction_query_mam_window = matching_window

    confirmed = await bot._redaction_verify_mam_room(
        TEST_ROOM_JID,
        {TEST_STANZA_1: timestamp},
    )

    assert confirmed == {TEST_STANZA_1}
    assert exact_queries == [(TEST_ROOM_JID, [TEST_STANZA_1])]
    assert window_queries == [
        (
            TEST_ROOM_JID,
            timestamp - 5,
            timestamp + 5,
        )
    ]


@pytest.mark.asyncio
async def test_raw_bodyless_moderation_handler_sets_pending_confirmation() -> None:
    bot = RedactionBot()
    confirmation = asyncio.Event()
    key = (TEST_ROOM_JID.lower(), TEST_STANZA_1)
    bot._redaction_confirmation_waiters = {key: {confirmation}}
    message = fake_moderation_confirmation(TEST_ROOM_JID, TEST_STANZA_1)

    assert message["body"] == ""

    bot._handle_redaction_confirmation_stanza(message)

    assert confirmation.is_set() is True


def test_incoming_filter_confirms_bodyless_message_and_preserves_stanza() -> None:
    bot = RedactionBot()
    confirmation = asyncio.Event()
    key = (TEST_ROOM_JID.lower(), TEST_STANZA_1)
    bot._redaction_confirmation_waiters = {key: {confirmation}}
    message = fake_moderation_confirmation(TEST_ROOM_JID, TEST_STANZA_1)

    returned = bot._redaction_incoming_filter(message)

    assert returned is message
    assert confirmation.is_set() is True


def test_incoming_filter_ignores_non_message_stanza() -> None:
    bot = RedactionBot()
    stanza = FakeMessage(TEST_ROOM_JID, "", body="")
    stanza.xml.tag = "{jabber:client}presence"

    returned = bot._redaction_incoming_filter(stanza)

    assert returned is stanza


def test_redaction_confirmation_ids_support_tombstone_and_moderate_layouts() -> None:
    tombstone = FakeMessage(TEST_ROOM_JID, "", body="")
    retracted = ET.SubElement(
        tombstone.xml,
        f"{{{RETRACT_NS}}}retracted",
        {"id": TEST_STANZA_1},
    )
    ET.SubElement(retracted, f"{{{MODERATE_NS}}}moderated")

    echoed = FakeMessage(TEST_ROOM_JID, "", body="")
    moderate = ET.SubElement(
        echoed.xml,
        f"{{{MODERATE_NS}}}moderate",
        {"id": TEST_STANZA_2},
    )
    ET.SubElement(moderate, f"{{{RETRACT_NS}}}retract")

    assert RedactionMixin._redaction_confirmation_ids(tombstone) == {TEST_STANZA_1}
    assert RedactionMixin._redaction_confirmation_ids(echoed) == {TEST_STANZA_2}


@pytest.mark.asyncio
async def test_redaction_accepts_future_returned_by_slixmpp_iq_send(temp_db_path) -> None:
    bot = RedactionBot()
    bot._test_iq_returns_future = True
    await setup_and_validate_redaction_test_db(bot, temp_db_path)
    try:
        await bot._redaction_index_message(
            FakeMessage(TEST_ROOM_JID, TEST_SENDER_NICK, TEST_STANZA_1)
        )
        await bot.flush_redaction_index()

        summary = await bot.redact_jid_messages(
            TEST_SENDER_JID,
            reason=TEST_REDACTION_REASON,
            actor=TEST_ACTOR_JID,
            announce=False,
        )

        assert summary["redacted"] == 1
        assert summary["failed"] == 0
        assert bot._redaction_confirmation_waiters == {}
    finally:
        await bot.db.close()


@pytest.mark.asyncio
async def test_future_iq_failure_is_consumed_when_broadcast_confirms_redaction(
    temp_db_path,
) -> None:
    bot = RedactionBot()
    bot._test_iq_returns_future = True
    bot._test_iq_send_delay = TEST_IQ_SEND_DELAY_SECONDS
    bot.fail_stanza_ids = {TEST_STANZA_1}
    await setup_and_validate_redaction_test_db(bot, temp_db_path)
    try:
        await bot._redaction_index_message(
            FakeMessage(TEST_ROOM_JID, TEST_SENDER_NICK, TEST_STANZA_1)
        )
        await bot.flush_redaction_index()

        loop_errors = []
        loop = asyncio.get_running_loop()
        previous_handler = loop.get_exception_handler()
        loop.set_exception_handler(lambda _loop, context: loop_errors.append(context))
        try:
            redaction_task = asyncio.create_task(
                bot.redact_jid_messages(
                    TEST_SENDER_JID,
                    reason=TEST_REDACTION_REASON,
                    actor=TEST_ACTOR_JID,
                    announce=False,
                )
            )
            key = (TEST_ROOM_JID.lower(), TEST_STANZA_1)
            for _ in range(100):
                if key in getattr(bot, "_redaction_confirmation_waiters", {}):
                    break
                await asyncio.sleep(0.001)
            else:
                raise AssertionError("redaction confirmation waiter was not registered")

            await bot.on_redaction_confirmation_message(
                fake_moderation_confirmation(TEST_ROOM_JID, TEST_STANZA_1)
            )
            summary = await redaction_task
            await asyncio.sleep(TEST_IQ_SEND_DELAY_SECONDS * 2)
        finally:
            loop.set_exception_handler(previous_handler)

        assert summary["redacted"] == 1
        assert summary["failed"] == 0
        assert loop_errors == []
        assert bot._redaction_confirmation_waiters == {}
    finally:
        await bot.db.close()


@pytest.mark.asyncio
async def test_redact_rows_accepts_live_confirmation_when_iq_reports_failure(
    temp_db_path,
) -> None:
    bot = RedactionBot()
    await setup_and_validate_redaction_test_db(bot, temp_db_path)
    try:
        await bot._redaction_index_message(
            FakeMessage(TEST_ROOM_JID, TEST_SENDER_NICK, TEST_STANZA_1)
        )
        await bot.flush_redaction_index()
        bot.fail_stanza_ids = {TEST_STANZA_1}
        bot.fail_stanza_errors = {
            TEST_STANZA_1: TEST_SERVER_REJECTED_IQ_ERROR.format(stanza_id=TEST_STANZA_1)
        }

        redaction_task = asyncio.create_task(
            bot.redact_jid_messages(
                TEST_SENDER_JID,
                reason=TEST_REDACTION_REASON,
                actor=TEST_ACTOR_JID,
                announce=False,
            )
        )

        key = (TEST_ROOM_JID.lower(), TEST_STANZA_1)
        for _ in range(100):
            if key in getattr(bot, "_redaction_confirmation_waiters", {}):
                break
            await asyncio.sleep(0.001)
        else:
            raise AssertionError("redaction confirmation waiter was not registered")

        await bot.on_redaction_confirmation_message(
            fake_moderation_confirmation(TEST_ROOM_JID, TEST_STANZA_1)
        )
        summary = await redaction_task

        assert summary["redacted"] == 1
        assert summary["failed"] == 0
        assert summary["skipped"] == 0
        assert bot._redaction_confirmation_waiters == {}

        cursor = await bot.db.execute(
            "SELECT redacted_at FROM redaction_index WHERE stanza_id = ?",
            (TEST_STANZA_1,),
        )
        row = await cursor.fetchone()
        assert row is not None and row[0] is not None
    finally:
        await bot.db.close()


async def setup_and_validate_redaction_test_db(
    bot: RedactionBot, temp_db_path: Path | str
) -> None:
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
async def test_setup_and_validate_redaction_test_db_rejects_mismatched_db_file(tmp_path, monkeypatch) -> None:
    import config

    monkeypatch.setattr(config, "DB_FILE", str(tmp_path / "configured.db"), raising=False)

    with pytest.raises(ValueError, match="temp_db_path must match config.DB_FILE"):
        await setup_and_validate_redaction_test_db(RedactionBot(), tmp_path / "actual.db")


@pytest.mark.asyncio
async def test_redaction_indexes_message_with_room_stanza_id(temp_db_path):
    bot = RedactionBot()
    await setup_and_validate_redaction_test_db(bot, temp_db_path)
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
    await setup_and_validate_redaction_test_db(bot, temp_db_path)
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
    await setup_and_validate_redaction_test_db(bot, temp_db_path)
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
    await setup_and_validate_redaction_test_db(bot, temp_db_path)
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
    await setup_and_validate_redaction_test_db(bot, temp_db_path)
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
    await setup_and_validate_redaction_test_db(bot, temp_db_path)
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
    await setup_and_validate_redaction_test_db(bot, temp_db_path)
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
    await setup_and_validate_redaction_test_db(bot, temp_db_path)
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
    await setup_and_validate_redaction_test_db(bot, temp_db_path)
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
    await setup_and_validate_redaction_test_db(bot, temp_db_path)
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


def test_auto_reason_matching_requires_word_or_phrase_boundaries():
    bot = RedactionBot()
    bot.redaction_auto_reasons = ["troll", "spam", "cp", "open-reg"]

    assert bot._redaction_auto_reason_matches("trollish/annoying behavior") is None
    assert bot._redaction_auto_reason_matches("spammy account") is None
    assert bot._redaction_auto_reason_matches("typescript issue") is None
    assert bot._redaction_auto_reason_matches("repeated troll behavior") == "troll"
    assert bot._redaction_auto_reason_matches("blocked for open-reg abuse") == "open-reg"


@pytest.mark.asyncio
async def test_redact_rows_uses_bounded_concurrency_and_batch_marks_rows(temp_db_path):
    bot = RedactionBot()
    bot.redaction_retract_concurrency = 2
    bot._test_iq_send_delay = TEST_IQ_SEND_DELAY_SECONDS * 4
    await setup_and_validate_redaction_test_db(bot, temp_db_path)
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
async def test_redact_rows_counts_timeouts_as_unconfirmed_not_failed(temp_db_path):
    bot = RedactionBot()
    await setup_and_validate_redaction_test_db(bot, temp_db_path)
    try:
        await bot._redaction_index_message(
            FakeMessage(TEST_ROOM_JID, TEST_SENDER_NICK, TEST_STANZA_1)
        )

        async def timeout_retract(_room, _stanza_id, _reason):
            raise asyncio.TimeoutError()

        bot._redaction_send_retract = timeout_retract
        summary = await bot.redact_jid_messages(
            TEST_SENDER_JID,
            reason=TEST_REDACTION_REASON,
            actor=TEST_ACTOR_JID,
            announce=False,
        )

        assert summary["found"] == 1
        assert summary["redacted"] == 0
        assert summary["unconfirmed"] == 1
        assert summary["failed"] == 0
        assert summary["skipped"] == 0
        assert await redacted_index_count(bot) == 0
        assert bot.alerts == []

        message = bot._redaction_summary_text(
            "Auto-redaction completed after ban",
            TEST_SENDER_JID,
            TEST_REDACTION_REASON,
            summary,
        )
        assert "Unconfirmed: 1" in message
        assert "Failed: 0" in message
        assert "They are not counted as" in message
    finally:
        await bot.db.close()


@pytest.mark.asyncio
async def test_redact_rows_marks_mam_verified_timeouts_as_redacted(temp_db_path):
    bot = RedactionBot()
    await setup_and_validate_redaction_test_db(bot, temp_db_path)
    try:
        await bot._redaction_index_message(
            FakeMessage(TEST_ROOM_JID, TEST_SENDER_NICK, TEST_STANZA_1)
        )

        async def timeout_retract(_room, _stanza_id, _reason):
            raise asyncio.TimeoutError()

        verified_targets = []

        async def verify_mam(targets):
            verified_targets.extend(targets)
            return {(TEST_ROOM_JID.lower(), TEST_STANZA_1)}

        bot._redaction_send_retract = timeout_retract
        bot._redaction_verify_mam_tombstones = verify_mam

        summary = await bot.redact_jid_messages(
            TEST_SENDER_JID,
            reason=TEST_REDACTION_REASON,
            actor=TEST_ACTOR_JID,
            announce=False,
        )

        assert summary["found"] == 1
        assert summary["redacted"] == 1
        assert summary["verified_via_mam"] == 1
        assert summary["unconfirmed"] == 0
        assert summary["failed"] == 0
        assert await redacted_index_count(bot) == 1
        assert len(verified_targets) == 1
        assert verified_targets[0][:2] == (TEST_ROOM_JID, TEST_STANZA_1)
        assert isinstance(verified_targets[0][2], int)

        message = bot._redaction_summary_text(
            "Auto-redaction completed after ban",
            TEST_SENDER_JID,
            TEST_REDACTION_REASON,
            summary,
        )
        assert "Redacted: 1" in message
        assert "Verified via MAM: 1" in message
        assert "Unconfirmed: 0" in message
    finally:
        await bot.db.close()


@pytest.mark.asyncio
async def test_redact_rows_counts_failed_retractions_without_marking_rows(temp_db_path):
    bot = RedactionBot()
    bot.fail_stanza_ids = {TEST_STANZA_2}
    await setup_and_validate_redaction_test_db(bot, temp_db_path)
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
    await setup_and_validate_redaction_test_db(bot, temp_db_path)
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
    await setup_and_validate_redaction_test_db(bot, temp_db_path)
    try:
        await bot._redaction_index_message(FakeMessage(TEST_ROOM_JID, TEST_SENDER_NICK, TEST_STANZA_1))
        await bot._redaction_index_message(FakeMessage(TEST_ROOM_JID, TEST_SENDER_NICK, TEST_STANZA_2))

        await bot.maybe_auto_redact_after_ban(TEST_SENDER_JID, TEST_MATCHING_BAN_REASON, actor=TEST_ACTOR_JID)

        assert_all_failed_auto_redaction_summary(latest_message_body(bot), found=2)
        assert bot.alerts == []
    finally:
        await bot.db.close()
