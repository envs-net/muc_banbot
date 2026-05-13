"""Additional RTBL command and publish-feed tests."""

from __future__ import annotations

import pytest

aiosqlite = pytest.importorskip("aiosqlite")

from banbot.cache import CacheMixin
from banbot.db import DatabaseMixin
from banbot.rtbl_cmd import RtblCommandMixin
from banbot.rtbl_db import RtblDatabaseMixin
from banbot.rtbl_publish import RtblPublishMixin
from banbot.rtbl_utils import _rtbl_build_payload, _rtbl_hash_jid


class FakePubSub:
    def __init__(self):
        self.published = []
        self.retracted = []

    async def publish(self, service, node, id=None, payload=None):
        self.published.append((service, node, id, payload))

    async def retract(self, service, node, id=None, notify=False):
        self.retracted.append((service, node, id, notify))


class RtblCmdBot(DatabaseMixin, CacheMixin, RtblDatabaseMixin, RtblCommandMixin, RtblPublishMixin):
    def __init__(self):
        self.protected_rooms = set()
        self.ban_cache = {}
        self.ban_index_by_jid = {}
        self.ban_index_by_nick = {}
        self.ban_index_by_domain = {}
        self.sent = []
        self.command_prefix = "!"
        self.rtbl_enabled = True
        self.rtbl_subscriptions = []
        self.rtbl_hash_cache = {}
        self.rtbl_domain_cache = {}
        self.rtbl_last_fetch = {}
        self.rtbl_last_change = {}
        self.rtbl_last_error = {}
        self.rtbl_publish_enabled = False
        self.rtbl_publish_service = "pubsub.example.org"
        self.rtbl_publish_jid_node = "muc_bans_sha256"
        self.rtbl_publish_domain_node = "muc_bans_domains"
        self.plugin = {"xep_0060": FakePubSub()}
        self.event_handlers = []
        self.fetched = []
        self.audit_events = []
        self.logged = []

    @staticmethod
    def bare_jid(jid: str) -> str:
        return str(jid).split("/", 1)[0].lower()

    _rtbl_hash_jid = staticmethod(_rtbl_hash_jid)
    _rtbl_build_payload = staticmethod(_rtbl_build_payload)

    async def bot_send_message(self, **kwargs):
        self.sent.append(kwargs)

    def add_event_handler(self, name, handler):
        self.event_handlers.append((name, handler))

    async def _on_rtbl_publish(self, *args, **kwargs):
        pass

    async def _on_rtbl_retract(self, *args, **kwargs):
        pass

    async def _rtbl_subscribe_and_fetch(self, service_jid, node):
        self.subscribed_fetch = (service_jid, node)

    async def _rtbl_subscribe_node(self, service_jid, node):
        return True, None

    async def _rtbl_fetch_all_items(self, service_jid, node, scan_occupants=True):
        self.fetched.append((service_jid, node, scan_occupants))

    async def _rtbl_cleanup_stale_persisted_bans(self, issuer="rtbl"):
        self.cleanup_issuer = issuer
        return 2

    def log_event(self, level, event, **fields):
        self.logged.append((level, event, fields))

    async def audit_event(self, event_type, **kwargs):
        self.audit_events.append((event_type, kwargs))


@pytest.mark.asyncio
async def test_rtbl_list_add_refresh_and_delete_flow(temp_db_path):
    bot = RtblCmdBot()
    await bot.setup_db()
    try:
        await bot.setup_rtbl()
        await bot.cmd_rtbl(["list"], "admin@conference.example.org", actor="admin@example.org")
        assert "RTBL Subscriptions" in bot.sent[-1]["mbody"]

        await bot.cmd_rtbl(["add", "xmppbl.org", "muc_bans_sha256"], "admin@conference.example.org", actor="admin@example.org")
        assert ("xmppbl.org", "muc_bans_sha256") in bot.rtbl_subscriptions
        assert bot.fetched[-1] == ("xmppbl.org", "muc_bans_sha256", True)
        assert bot.audit_events[-1][0] == "rtbl_subscription_added"

        await bot.db.execute(
            "INSERT INTO rtbl_hashes (hash, service_jid, node, reason) VALUES (?, ?, ?, ?)",
            ("a" * 64, "xmppbl.org", "muc_bans_sha256", "spam"),
        )
        await bot.db.commit()
        await bot.cmd_rtbl(["refresh", "xmppbl.org", "muc_bans_sha256"], "admin@conference.example.org")
        assert "Refresh complete" in bot.sent[-1]["mbody"]

        await bot.cmd_rtbl(["delete", "xmppbl.org", "muc_bans_sha256"], "admin@conference.example.org", actor="admin@example.org")
        assert "Removed" in bot.sent[-1]["mbody"]
        assert bot.cleanup_issuer == "rtbl_delete"
        assert bot.audit_events[-1][0] == "rtbl_subscription_removed"
    finally:
        await bot.db.close()


@pytest.mark.asyncio
async def test_rtbl_rejects_invalid_or_own_subscription(temp_db_path):
    bot = RtblCmdBot()
    await bot.setup_db()
    try:
        await bot.setup_rtbl()
        await bot.cmd_rtbl(["add", "not-a-service", "node"], "admin@conference.example.org")
        assert "Invalid RTBL service" in bot.sent[-1]["mbody"]

        bot.rtbl_publish_enabled = True
        await bot.cmd_rtbl(["add", "pubsub.example.org", "muc_bans_sha256"], "admin@conference.example.org")
        assert "Refusing to subscribe to own publish node" in bot.sent[-1]["mbody"]
    finally:
        await bot.db.close()


@pytest.mark.asyncio
async def test_rtbl_publish_status_sync_and_single_publish_retract(temp_db_path):
    bot = RtblCmdBot()
    bot.rtbl_publish_enabled = True
    await bot.setup_db()
    try:
        await bot.setup_rtbl()
        await bot.upsert_ban_db("User@Example.org/resource", "Nick", 0, "admin", "spam")
        await bot.upsert_ban_db("*.Example.org", None, 0, "admin", "domain spam")

        await bot.cmd_rtbl(["publish", "status"], "admin@conference.example.org")
        assert "RTBL Publish status" in bot.sent[-1]["mbody"]
        assert "1 active bans" in bot.sent[-1]["mbody"]

        await bot.cmd_rtbl(["publish", "sync"], "admin@conference.example.org")
        assert "Sync complete" in bot.sent[-1]["mbody"]
        assert len(bot.plugin["xep_0060"].published) >= 2

        await bot.rtbl_publish_ban("Another@Example.org/resource", None, "single")
        expected_hash = _rtbl_hash_jid("another@example.org")
        assert any(item[2] == expected_hash for item in bot.plugin["xep_0060"].published)

        await bot.rtbl_retract_ban("Another@Example.org/resource", "example.org")
        assert any(item[2] == expected_hash for item in bot.plugin["xep_0060"].retracted)
        assert any(item[2] == "example.org" for item in bot.plugin["xep_0060"].retracted)
    finally:
        await bot.db.close()
