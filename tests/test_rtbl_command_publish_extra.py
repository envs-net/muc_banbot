"""Additional RTBL command and publish-feed tests."""

from __future__ import annotations

import pytest

aiosqlite = pytest.importorskip("aiosqlite")

from banbot.cache import CacheMixin
from banbot.db import DatabaseMixin
from banbot.commands.rtbl_admin import RtblCommandMixin
from banbot.rtbl.db import RtblDatabaseMixin
from banbot.rtbl.publish import RtblPublishMixin
from banbot.rtbl.utils import _rtbl_build_payload, _rtbl_hash_jid


class FakeForm:
    def __init__(self):
        self.fields = []

    def add_field(self, **kwargs):
        self.fields.append(kwargs)

    def field_value(self, var):
        for field in self.fields:
            if field.get("var") == var:
                return field.get("value")
        return None


class FakeXep0004:
    def make_form(self, ftype="submit"):
        return FakeForm()


class FakePubSub:
    def __init__(self):
        self.published = []
        self.retracted = []
        self.configured = []
        self.created = []
        self.fail_publish_nodes = set()
        self.fail_get_items_nodes = set()
        self.fail_retract_nodes = set()
        self.hidden_item_ids = set()

    async def get_node_config(self, service, node):
        return {}

    async def get_items(self, service, node, max_items=1, item_ids=None):
        if node in self.fail_get_items_nodes:
            raise RuntimeError("fetch forbidden")

        visible = [
            {"id": item_id}
            for published_service, published_node, item_id, _payload in self.published
            if published_service == service
            and published_node == node
            and item_id not in self.hidden_item_ids
            and (service, node, item_id, False) not in self.retracted
            and (service, node, item_id, True) not in self.retracted
        ]

        if item_ids is not None:
            wanted = set(item_ids)
            return [item for item in visible if item["id"] in wanted]

        return visible[:max_items]

    async def create_node(self, service, node):
        self.created.append((service, node))

    async def set_node_config(self, service, node, config=None):
        self.configured.append((service, node, config))

    async def publish(self, service, node, id=None, payload=None):
        if node in self.fail_publish_nodes:
            raise RuntimeError("publish forbidden")
        self.published.append((service, node, id, payload))

    async def retract(self, service, node, id=None, notify=False):
        if node in self.fail_retract_nodes:
            raise RuntimeError("retract forbidden")
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
        self.rtbl_publish_config_enabled = False
        self.rtbl_publish_enabled = False
        self.rtbl_publish_sanity_check_ok = None
        self.rtbl_publish_disabled_reason = None
        self.rtbl_publish_service = "pubsub.example.org"
        self.rtbl_publish_jid_node = "muc_bans_sha256"
        self.rtbl_publish_domain_node = "muc_bans_domains"
        self.plugin = {"xep_0060": FakePubSub(), "xep_0004": FakeXep0004()}
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

    def register_plugin(self, name):
        if name == "xep_0004":
            self.plugin[name] = FakeXep0004()
        else:
            raise KeyError(name)

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
async def test_rtbl_list_supports_paging_and_all(temp_db_path):
    bot = RtblCmdBot()
    bot.list_page_size = 2
    await bot.setup_db()
    try:
        await bot.setup_rtbl()
        bot.rtbl_subscriptions = [
            ("rtbl_service_1", "node1"),
            ("rtbl_service_2", "node2"),
            ("rtbl_service_3", "node3"),
        ]

        await bot.cmd_rtbl(["list"], "admin@conference.example.org")
        body = bot.sent[-1]["mbody"]
        assert "RTBL Subscriptions (3) - Page 1/2" in body
        assert "rtbl_service_1  /  node1" in body
        assert "rtbl_service_2  /  node2" in body
        assert "rtbl_service_3  /  node3" not in body
        assert "Use !rtbl list 2 for the next page" in body

        await bot.cmd_rtbl(["list", "2"], "admin@conference.example.org")
        body = bot.sent[-1]["mbody"]
        assert "RTBL Subscriptions (3) - Page 2/2" in body
        assert "rtbl_service_3  /  node3" in body
        assert "rtbl_service_1  /  node1" not in body

        await bot.cmd_rtbl(["list", "last"], "admin@conference.example.org")
        assert "RTBL Subscriptions (3) - Page 2/2" in bot.sent[-1]["mbody"]

        await bot.cmd_rtbl(["list", "all"], "admin@conference.example.org")
        body = bot.sent[-1]["mbody"]
        assert "RTBL Subscriptions (3) - All" in body
        assert "rtbl_service_1  /  node1" in body
        assert "rtbl_service_2  /  node2" in body
        assert "rtbl_service_3  /  node3" in body
    finally:
        await bot.db.close()


@pytest.mark.asyncio
async def test_rtbl_list_rejects_invalid_page_argument(temp_db_path):
    bot = RtblCmdBot()
    await bot.setup_db()
    try:
        await bot.cmd_rtbl(["list", "nope"], "admin@conference.example.org")
        assert "Usage: !rtbl list [all|page|last]" in bot.sent[-1]["mbody"]
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

def _last_config_value(bot, node, var):
    for _service, configured_node, form in reversed(bot.plugin["xep_0060"].configured):
        if configured_node == node:
            return form.field_value(var)
    return None


def test_rtbl_publish_required_max_items_rounds_to_1000_steps():
    bot = RtblCmdBot()

    assert bot._rtbl_publish_required_max_items(0) == 1000
    assert bot._rtbl_publish_required_max_items(1) == 1000
    assert bot._rtbl_publish_required_max_items(999) == 1000
    assert bot._rtbl_publish_required_max_items(1000) == 1000
    assert bot._rtbl_publish_required_max_items(1001) == 2000
    assert bot._rtbl_publish_required_max_items(2000) == 2000
    assert bot._rtbl_publish_required_max_items(2001) == 3000


def test_rtbl_make_node_config_form_uses_dynamic_max_items():
    bot = RtblCmdBot()

    form = bot._rtbl_make_node_config_form(max_items=1001)

    assert form.field_value("pubsub#max_items") == "2000"
    assert form.field_value("pubsub#persist_items") == "1"


@pytest.mark.asyncio
async def test_setup_rtbl_publish_configures_nodes_for_active_ban_counts(temp_db_path):
    bot = RtblCmdBot()
    bot.rtbl_publish_enabled = True
    await bot.setup_db()
    try:
        for index in range(1001):
            await bot.upsert_ban_db(
                f"user{index}@example.org",
                None,
                0,
                "admin",
                "bulk jid",
            )
        await bot.upsert_ban_db("*.example.org", None, 0, "admin", "domain")

        await bot.setup_rtbl_publish()

        assert _last_config_value(bot, "muc_bans_sha256", "pubsub#max_items") == "2000"
        assert _last_config_value(bot, "muc_bans_domains", "pubsub#max_items") == "1000"
        assert bot.rtbl_publish_jid_max_items == 2000
        assert bot.rtbl_publish_domain_max_items == 1000
        assert bot.rtbl_publish_sanity_check_ok is True
        assert bot.rtbl_publish_disabled_reason is None
    finally:
        await bot.db.close()


@pytest.mark.asyncio
async def test_rtbl_publish_ban_auto_grows_node_capacity(temp_db_path):
    bot = RtblCmdBot()
    bot.rtbl_publish_enabled = True
    await bot.setup_db()
    try:
        # Simulate a node configured at the old floor, then add enough active
        # local bans so the next single publish needs the node to grow.
        bot.rtbl_publish_jid_max_items = 1000
        for index in range(1001):
            await bot.upsert_ban_db(
                f"existing{index}@example.org",
                None,
                0,
                "admin",
                "bulk jid",
            )

        await bot.rtbl_publish_ban("new@example.org/resource", None, "single")

        assert _last_config_value(bot, "muc_bans_sha256", "pubsub#max_items") == "2000"
        assert bot.rtbl_publish_jid_max_items == 2000
        expected_hash = _rtbl_hash_jid("new@example.org")
        assert any(item[2] == expected_hash for item in bot.plugin["xep_0060"].published)
    finally:
        await bot.db.close()

@pytest.mark.asyncio
async def test_setup_rtbl_publish_disables_publish_when_sanity_check_publish_fails(temp_db_path):
    bot = RtblCmdBot()
    bot.rtbl_publish_enabled = True
    bot.rtbl_announce = True
    bot.plugin["xep_0060"].fail_publish_nodes.add("muc_bans_sha256")
    await bot.setup_db()
    try:
        await bot.upsert_ban_db("user@example.org", None, 0, "admin", "local")

        await bot.setup_rtbl_publish()

        assert bot.rtbl_publish_enabled is False
        assert bot.rtbl_publish_config_enabled is True
        assert bot.rtbl_publish_sanity_check_ok is False
        assert "muc_bans_sha256" in bot.rtbl_publish_disabled_reason
        assert "test publish failed" in bot.rtbl_publish_disabled_reason
        assert "RTBL Publish disabled" in bot.sent[-1]["mbody"]
        expected_hash = _rtbl_hash_jid("user@example.org")
        assert not any(item[2] == expected_hash for item in bot.plugin["xep_0060"].published)
    finally:
        await bot.db.close()


@pytest.mark.asyncio
async def test_setup_rtbl_publish_disables_publish_when_sanity_item_is_not_visible(temp_db_path):
    bot = RtblCmdBot()
    bot.rtbl_publish_enabled = True
    await bot.setup_db()
    try:
        # Hide every sanity-check item after it was published. The check should
        # still attempt cleanup and then disable own RTBL publishing.
        original_publish = bot.plugin["xep_0060"].publish

        async def publish_and_hide(service, node, id=None, payload=None):
            await original_publish(service, node, id=id, payload=payload)
            bot.plugin["xep_0060"].hidden_item_ids.add(id)

        bot.plugin["xep_0060"].publish = publish_and_hide

        await bot.setup_rtbl_publish()

        assert bot.rtbl_publish_enabled is False
        assert any(item[1] == "muc_bans_sha256" for item in bot.plugin["xep_0060"].retracted)
        assert any(item[1] == "muc_bans_domains" for item in bot.plugin["xep_0060"].retracted)
    finally:
        await bot.db.close()


@pytest.mark.asyncio
async def test_setup_rtbl_publish_disables_publish_when_sanity_retract_fails(temp_db_path):
    bot = RtblCmdBot()
    bot.rtbl_publish_enabled = True
    bot.plugin["xep_0060"].fail_retract_nodes.add("muc_bans_domains")
    await bot.setup_db()
    try:
        await bot.setup_rtbl_publish()

        assert bot.rtbl_publish_enabled is False
        assert any(item[1] == "muc_bans_sha256" for item in bot.plugin["xep_0060"].retracted)
    finally:
        await bot.db.close()
