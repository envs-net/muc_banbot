from __future__ import annotations

from xml.etree import ElementTree as ET

import pytest

pytest.importorskip("slixmpp")
aiosqlite = pytest.importorskip("aiosqlite")

from banbot.rtbl_db import RtblDatabaseMixin
from banbot.rtbl_pubsub import RtblPubSubMixin
from banbot.rtbl_utils import _rtbl_extract_reason

PUBSUB = "http://jabber.org/protocol/pubsub"
RSM = "http://jabber.org/protocol/rsm"
REPORTING = "urn:xmpp:reporting:1"
HASH_A = "a" * 64
HASH_B = "b" * 64
HASH_C = "c" * 64


class FakeResult:
    def __init__(self, xml):
        self.xml = xml


class FakeIQ:
    def __init__(self, results):
        self.xml = ET.Element("iq")
        self._results = results

    async def send(self):
        next_result = self._results.pop(0)
        if isinstance(next_result, Exception):
            raise next_result
        return next_result


class RtblBot(RtblDatabaseMixin, RtblPubSubMixin):
    def __init__(self, db, results):
        self.db = db
        self._results = list(results)
        self.rtbl_enabled = True
        self.rtbl_announce = False
        self.rtbl_subscriptions = []
        self.rtbl_hash_cache = {}
        self.rtbl_domain_cache = {}
        self.rtbl_last_fetch = {}
        self.rtbl_last_counts = {}
        self.rtbl_last_error = {}
        self.rtbl_last_change = {}
        self.cleanup_calls = []
        self.occupant_checks = []
        self.sent = []
        self.plugin = {"xep_0060": object()}

    def make_iq_get(self, ito=None):
        return FakeIQ(self._results)

    def _rtbl_extract_reason(self, payload):
        return _rtbl_extract_reason(payload)

    async def _rtbl_cleanup_stale_persisted_bans(self, issuer="rtbl_refresh"):
        self.cleanup_calls.append(issuer)
        return 2

    async def _rtbl_check_all_occupants_against_caches(self, source):
        self.occupant_checks.append(source)

    async def bot_send_message(self, **kwargs):
        self.sent.append(kwargs)


def items_result(item_ids, *, rsm_last=None):
    iq = ET.Element("iq")
    pubsub = ET.SubElement(iq, f"{{{PUBSUB}}}pubsub")
    items = ET.SubElement(pubsub, f"{{{PUBSUB}}}items", {"node": "node"})
    for item_id in item_ids:
        item = ET.SubElement(items, f"{{{PUBSUB}}}item", {"id": item_id})
        report = ET.SubElement(item, f"{{{REPORTING}}}report")
        text = ET.SubElement(report, f"{{{REPORTING}}}text")
        text.text = f"reason-{item_id[:1]}"
    if rsm_last is not None:
        rsm = ET.SubElement(iq, f"{{{RSM}}}set")
        last = ET.SubElement(rsm, f"{{{RSM}}}last")
        last.text = rsm_last
    return FakeResult(iq)


def malformed_result():
    return FakeResult(ET.Element("iq"))


async def prepare_rtbl_db(tmp_path):
    db = await aiosqlite.connect(tmp_path / "rtbl.sqlite3")
    await db.execute(
        """
        CREATE TABLE rtbl_hashes (
            hash TEXT NOT NULL,
            service_jid TEXT NOT NULL,
            node TEXT NOT NULL,
            reason TEXT,
            created_at INTEGER NOT NULL DEFAULT (strftime('%s','now')),
            PRIMARY KEY (hash, service_jid, node)
        )
        """
    )
    await db.execute("CREATE INDEX idx_rtbl_hashes_hash ON rtbl_hashes(hash)")
    await db.execute(
        """
        CREATE TABLE rtbl_domains (
            domain TEXT NOT NULL,
            service_jid TEXT NOT NULL,
            node TEXT NOT NULL,
            reason TEXT,
            created_at INTEGER NOT NULL DEFAULT (strftime('%s','now')),
            PRIMARY KEY (domain, service_jid, node)
        )
        """
    )
    await db.execute("CREATE INDEX idx_rtbl_domains_domain ON rtbl_domains(domain)")
    await db.commit()
    return db


async def fetch_hashes(db):
    async with db.execute("SELECT hash FROM rtbl_hashes ORDER BY hash") as cursor:
        return [row[0] for row in await cursor.fetchall()]


@pytest.mark.rtbl
@pytest.mark.asyncio
async def test_successful_fetch_reconciles_stale_hashes_and_domains(tmp_path):
    db = await prepare_rtbl_db(tmp_path)
    try:
        await db.execute("INSERT INTO rtbl_hashes (hash, service_jid, node, reason) VALUES (?, ?, ?, ?)", (HASH_A, "service", "node", "old"))
        await db.execute("INSERT INTO rtbl_hashes (hash, service_jid, node, reason) VALUES (?, ?, ?, ?)", (HASH_C, "service", "node", "stale"))
        await db.execute("INSERT INTO rtbl_domains (domain, service_jid, node, reason) VALUES (?, ?, ?, ?)", ("old.example", "service", "node", "stale"))
        await db.execute("INSERT INTO rtbl_hashes (hash, service_jid, node, reason) VALUES (?, ?, ?, ?)", (HASH_C, "other", "node", "keep"))
        await db.commit()

        bot = RtblBot(db, [items_result([HASH_A, HASH_B, "*.example.org"])])
        await bot._rtbl_fetch_all_items("service", "node", scan_occupants=True)

        assert await fetch_hashes(db) == [HASH_A, HASH_B, HASH_C]
        async with db.execute("SELECT domain FROM rtbl_domains") as cursor:
            domains = {row[0] for row in await cursor.fetchall()}
        assert domains == {"example.org"}
        assert bot.cleanup_calls == ["rtbl_refresh"]
        assert bot.occupant_checks == ["service/node"]
    finally:
        await db.close()


@pytest.mark.rtbl
@pytest.mark.asyncio
async def test_malformed_fetch_does_not_delete_stale_entries(tmp_path):
    db = await prepare_rtbl_db(tmp_path)
    try:
        await db.execute("INSERT INTO rtbl_hashes (hash, service_jid, node, reason) VALUES (?, ?, ?, ?)", (HASH_A, "service", "node", "keep"))
        await db.commit()

        bot = RtblBot(db, [malformed_result()])
        await bot._rtbl_fetch_all_items("service", "node", scan_occupants=False)

        assert await fetch_hashes(db) == [HASH_A]
        assert bot.cleanup_calls == []
        assert bot.rtbl_last_error[("service", "node")] == "missing pubsub element"
    finally:
        await db.close()


@pytest.mark.rtbl
@pytest.mark.asyncio
async def test_full_page_without_rsm_is_treated_as_incomplete(tmp_path):
    db = await prepare_rtbl_db(tmp_path)
    try:
        await db.execute("INSERT INTO rtbl_hashes (hash, service_jid, node, reason) VALUES (?, ?, ?, ?)", (HASH_C, "service", "node", "keep"))
        await db.commit()
        # page_size is 200 in the implementation, so exactly 200 without RSM must not cleanup.
        ids = [f"{i:064x}" for i in range(200)]
        bot = RtblBot(db, [items_result(ids)])
        await bot._rtbl_fetch_all_items("service", "node", scan_occupants=False)

        hashes = await fetch_hashes(db)
        assert HASH_C in hashes
        assert bot.cleanup_calls == []
        assert "incomplete" in bot.rtbl_last_error[("service", "node")]
    finally:
        await db.close()


class FakeFrom:
    def __init__(self, bare):
        self.bare = bare


class FakeEventItems(list):
    def __init__(self, node, items):
        super().__init__(items)
        self.node = node

    def __getitem__(self, key):
        if key == "node":
            return self.node
        return super().__getitem__(key)


class FakePubSubMessage(dict):
    def __init__(self, service, node, items):
        super().__init__()
        self["from"] = FakeFrom(service)
        self["pubsub_event"] = {"items": FakeEventItems(node, items)}


@pytest.mark.rtbl
@pytest.mark.asyncio
async def test_pubsub_publish_updates_hash_and_domain_caches_and_scans(tmp_path):
    db = await prepare_rtbl_db(tmp_path)
    try:
        bot = RtblBot(db, [])
        bot.rtbl_subscriptions = [("service", "node")]
        hash_checks = []
        domain_checks = []

        async def check_hash(item_id, reason):
            hash_checks.append((item_id, reason))

        async def check_domain(domain, reason):
            domain_checks.append((domain, reason))

        bot._rtbl_check_all_occupants_for_hash = check_hash
        bot._rtbl_check_all_occupants_for_domain = check_domain

        msg = FakePubSubMessage(
            "service",
            "node",
            [
                {"id": HASH_A, "payload": None},
                {"id": "*.Example.Org", "payload": None},
                {"id": "not-a-valid-entry", "payload": None},
            ],
        )

        await bot._on_rtbl_publish(msg)

        assert bot.rtbl_hash_cache[HASH_A] is None
        assert bot.rtbl_domain_cache["example.org"] is None
        assert hash_checks == [(HASH_A, None)]
        assert domain_checks == [("example.org", None)]
        assert bot.rtbl_last_error[("service", "node")] is None
    finally:
        await db.close()


@pytest.mark.rtbl
@pytest.mark.asyncio
async def test_pubsub_publish_ignores_unsubscribed_and_disabled_messages(tmp_path):
    db = await prepare_rtbl_db(tmp_path)
    try:
        bot = RtblBot(db, [])
        bot.rtbl_subscriptions = [("service", "node")]

        await bot._on_rtbl_publish(FakePubSubMessage("other", "node", [{"id": HASH_A}]))
        assert bot.rtbl_hash_cache == {}

        bot.rtbl_enabled = False
        await bot._on_rtbl_publish(FakePubSubMessage("service", "node", [{"id": HASH_A}]))
        assert bot.rtbl_hash_cache == {}
    finally:
        await db.close()


@pytest.mark.rtbl
@pytest.mark.asyncio
async def test_pubsub_retract_keeps_cache_when_other_subscription_still_references_item(tmp_path):
    db = await prepare_rtbl_db(tmp_path)
    try:
        await db.execute(
            "INSERT INTO rtbl_hashes (hash, service_jid, node, reason) VALUES (?, ?, ?, ?)",
            (HASH_A, "service", "node", "remove"),
        )
        await db.execute(
            "INSERT INTO rtbl_hashes (hash, service_jid, node, reason) VALUES (?, ?, ?, ?)",
            (HASH_A, "other", "node", "keep"),
        )
        await db.commit()

        bot = RtblBot(db, [])
        bot.rtbl_subscriptions = [("service", "node")]
        bot.rtbl_hash_cache[HASH_A] = "remove"

        await bot._on_rtbl_retract(FakePubSubMessage("service", "node", [{"id": HASH_A}]))

        assert bot.rtbl_hash_cache[HASH_A] == "remove"
        assert bot.cleanup_calls == ["rtbl_retract"]
        assert bot.rtbl_last_error[("service", "node")] is None
    finally:
        await db.close()


@pytest.mark.rtbl
@pytest.mark.asyncio
async def test_pubsub_retract_removes_domain_cache_when_last_reference_disappears(tmp_path):
    db = await prepare_rtbl_db(tmp_path)
    try:
        await db.execute(
            "INSERT INTO rtbl_domains (domain, service_jid, node, reason) VALUES (?, ?, ?, ?)",
            ("example.org", "service", "node", "remove"),
        )
        await db.commit()

        bot = RtblBot(db, [])
        bot.rtbl_subscriptions = [("service", "node")]
        bot.rtbl_domain_cache["example.org"] = "remove"

        await bot._on_rtbl_retract(FakePubSubMessage("service", "node", [{"id": "*.example.org"}]))

        assert "example.org" not in bot.rtbl_domain_cache
        assert bot.cleanup_calls == ["rtbl_retract"]
    finally:
        await db.close()
