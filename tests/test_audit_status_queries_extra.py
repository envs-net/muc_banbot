"""Additional audit/status/ban-query coverage."""

from __future__ import annotations

import logging
import time

import pytest

aiosqlite = pytest.importorskip("aiosqlite")

from banbot.audit import AuditMixin
from banbot.ban_queries import BanQueryMixin
from banbot.cache import CacheMixin
from banbot.config_cmd import ConfigCommandMixin
from banbot.db import DatabaseMixin
from banbot.rtbl_utils import _rtbl_hash_jid
from banbot.status import StatusMixin


class AuditStatusQueryBot(
    DatabaseMixin,
    CacheMixin,
    AuditMixin,
    BanQueryMixin,
    StatusMixin,
    ConfigCommandMixin,
):
    def __init__(self):
        self.protected_rooms = {"room@conference.example.test"}
        self.registered_rooms = set()
        self.ban_cache = {}
        self.ban_index_by_jid = {}
        self.ban_index_by_nick = {}
        self.ban_index_by_domain = {}
        self.sent = []
        self.command_prefix = "!"
        self.structured_event_logs = True
        self.audit_log_enabled = True
        self.audit_log_retention_days = 30
        self.last_audit_cleanup_count = 0
        self.last_audit_cleanup_run = 0
        self.last_import_backup_file = None
        self.last_version_check_result = "2.2.0"
        self.bot_start_time = int(time.time()) - 90
        self.server_connect_time = int(time.time()) - 30
        self.reconnecting = False
        self.last_reconnect_time = None
        self.unban_task = None
        self.health_check_task = None
        self.version_check_task = None
        self.version_check_enabled = False
        self.version_check_url = None
        self._rtbl_refresh_task = None
        self.rtbl_enabled = True
        self.rtbl_refresh_interval = 3600
        self.rtbl_subscriptions = [("xmppbl.org", "muc_bans_sha256")]
        self.rtbl_hash_cache = {"a" * 64: "spam"}
        self.rtbl_domain_cache = {"spam.example": "spam"}
        self.rtbl_publish_config_enabled = False
        self.rtbl_publish_enabled = False
        self.rtbl_publish_sanity_check_ok = None
        self.rtbl_publish_disabled_reason = None
        self.rtbl_publish_service = "pubsub.example.org"
        self.rtbl_publish_jid_node = "muc_bans_sha256"
        self.rtbl_publish_domain_node = "muc_bans_domains"
        self.admin_affiliation_query_forbidden_rooms = set()
        self.bot_admin_state = {"room@conference.example.test": True}
        self.occupants = {
            "admin@conference.example.org": {
                "Admin": {"jid": "admin@example.org/resource", "affiliation": "owner"},
            }
        }
        self.audit_logged = []
        self.log_level = "INFO"
        self.announce_startup = True
        self.announce_sync_details = True
        self.show_ban_in_muc = True
        self.allow_user_cmds = True
        self.health_check_interval = 60
        self.unban_check_interval = 60
        self.max_tempban_days = 30
        self.public_command_rate_limit_max = 3
        self.public_command_rate_limit_window = 30
        self.muc_write_limit = 4
        self.omemo_enabled = False
        self.rtbl_announce = True
        self.version_check_interval = 3600

    async def bot_send_message(self, **kwargs):
        self.sent.append(kwargs)

    @staticmethod
    def bare_jid(jid: str) -> str:
        return str(jid).split("/", 1)[0].lower()

    @staticmethod
    def safe_jid(jid: str) -> str:
        return str(jid).split("/", 1)[0]

    _rtbl_hash_jid = staticmethod(_rtbl_hash_jid)


def last_body(bot: AuditStatusQueryBot) -> str:
    return bot.sent[-1]["mbody"]


@pytest.mark.asyncio
async def test_audit_event_query_and_cleanup(temp_db_path):
    bot = AuditStatusQueryBot()
    await bot.setup_db()
    try:
        await bot.audit_event(
            "ban_applied",
            actor="admin@example.org",
            room="room@conference.example.test",
            target_type="jid",
            target="user@example.org",
            jid="user@example.org",
            nick="Spammer",
            comment="spam wave",
            details={"source": "test"},
        )
        await bot.cmd_audit(["spam"], "admin@conference.example.org")
        assert "Audit log" in last_body(bot)
        assert "ban_applied" in last_body(bot)
        assert "user@example.org" in last_body(bot)

        old_ts = int(time.time()) - 40 * 86400
        await bot.db.execute("UPDATE audit_log SET created_at = ?", (old_ts,))
        await bot.db.commit()
        deleted = await bot.cleanup_old_audit_logs()
        assert deleted == 1
    finally:
        await bot.db.close()


def test_log_event_plaintext_when_structured_disabled(caplog):
    bot = AuditStatusQueryBot()
    bot.structured_event_logs = False
    with caplog.at_level(logging.INFO):
        bot.log_event(logging.INFO, "test_event", target="user@example.org")
    assert "test_event" in caplog.text


@pytest.mark.asyncio
async def test_ban_queries_cover_search_list_rtbl_and_why(temp_db_path):
    bot = AuditStatusQueryBot()
    await bot.setup_db()
    try:
        await bot.upsert_ban_db("User@Example.org/resource", "Nick", 0, "admin@example.org", "spam wave")
        await bot.upsert_ban_db("*.Spam.Example", None, 0, "rtbl", "domain wave")
        await bot.load_bans_from_db()
        await bot.db.execute("""CREATE TABLE rtbl_domains (domain TEXT, service_jid TEXT, node TEXT, reason TEXT, created_at INTEGER DEFAULT 0)""")
        await bot.db.execute("""CREATE TABLE rtbl_hashes (hash TEXT, service_jid TEXT, node TEXT, reason TEXT, created_at INTEGER DEFAULT 0)""")
        await bot.db.execute(
            "INSERT INTO rtbl_domains (domain, service_jid, node, reason) VALUES (?, ?, ?, ?)",
            ("spam.example", "xmppbl.org", "spam_source_domains", "domain reason"),
        )
        await bot.db.execute(
            "INSERT INTO rtbl_hashes (hash, service_jid, node, reason) VALUES (?, ?, ?, ?)",
            (_rtbl_hash_jid("hashme@example.org"), "xmppbl.org", "muc_bans_sha256", "hash reason"),
        )
        await bot.audit_event("ban_applied", target="user@example.org", jid="user@example.org", comment="spam wave")
        await bot.db.commit()

        await bot.cmd_bansearch("reason:domain")
        assert "RTBL domains" in last_body(bot)
        assert "spam.example" in last_body(bot)

        await bot.cmd_bansearch("hashme@example.org")
        assert "matched JID hash" in last_body(bot)

        await bot.cmd_banlist("admin@conference.example.org")
        assert "User@Example.org" not in last_body(bot)
        assert "user@example.org" in last_body(bot)

        await bot.cmd_banlist_rtbl("admin@conference.example.org")
        assert "RTBL Banlist" in last_body(bot)
        assert "spam.example" in last_body(bot)

        await bot.cmd_why("user@example.org", "admin@conference.example.org")
        assert "spam wave" in last_body(bot)
        assert "Recent audit history" in last_body(bot)
    finally:
        await bot.db.close()


@pytest.mark.asyncio
async def test_status_and_config_outputs_include_operational_sections(temp_db_path, monkeypatch):
    bot = AuditStatusQueryBot()
    await bot.setup_db()
    try:
        await bot.set_public_policy_text("Be nice", enabled=True)
        await bot._cmd_config("admin@conference.example.org")
        assert "🪪 JID" in last_body(bot)
        assert "🔐 OMEMO Enabled" in last_body(bot)
        assert "🛡️ RTBL Enabled" in last_body(bot)

        # Avoid the one-second psutil sampling delay and host-specific values.
        import banbot.status as status_module

        class FakeProcess:
            def memory_info(self):
                return type("Mem", (), {"rss": 42 * 1024 * 1024})()

            def cpu_percent(self, interval):
                return 0.5

        monkeypatch.setattr(status_module.psutil, "Process", lambda pid: FakeProcess())
        monkeypatch.setattr(status_module.psutil, "getloadavg", lambda: (0.1, 0.2, 0.3))
        monkeypatch.setattr(status_module.psutil, "cpu_count", lambda: 8)

        await bot._cmd_status("admin@conference.example.org")
        body = last_body(bot)
        assert "Bot is online" in body
        assert "Bot Version" in body
        assert "RTBL Entries" in body
        assert "Protected Rooms" in body
        assert "Public Policy: enabled" in body
        assert "admin@example.org" in body
        assert "admin@example.org/resource" not in body
    finally:
        await bot.db.close()


@pytest.mark.asyncio
async def test_status_shows_rtbl_publish_runtime_disabled_reason(temp_db_path, monkeypatch):
    bot = AuditStatusQueryBot()
    bot.rtbl_publish_config_enabled = True
    bot.rtbl_publish_enabled = False
    bot.rtbl_publish_sanity_check_ok = False
    bot.rtbl_publish_disabled_reason = "muc_bans_sha256: test publish failed: forbidden"
    await bot.setup_db()
    try:
        import banbot.status as status_module

        class FakeProcess:
            def memory_info(self):
                return type("Mem", (), {"rss": 42 * 1024 * 1024})()

            def cpu_percent(self, interval):
                return 0.5

        monkeypatch.setattr(status_module.psutil, "Process", lambda pid: FakeProcess())
        monkeypatch.setattr(status_module.psutil, "getloadavg", lambda: (0.1, 0.2, 0.3))
        monkeypatch.setattr(status_module.psutil, "cpu_count", lambda: 8)

        await bot._cmd_status("admin@conference.example.org")
        body = last_body(bot)

        assert "RTBL Publish: ⚠️ disabled at runtime" in body
        assert "configured: enabled" in body
        assert "muc_bans_sha256: test publish failed: forbidden" in body
    finally:
        await bot.db.close()


@pytest.mark.asyncio
async def test_status_shows_rtbl_publish_sanity_check_ok(temp_db_path, monkeypatch):
    bot = AuditStatusQueryBot()
    bot.rtbl_publish_config_enabled = True
    bot.rtbl_publish_enabled = True
    bot.rtbl_publish_sanity_check_ok = True
    await bot.setup_db()
    try:
        import banbot.status as status_module

        class FakeProcess:
            def memory_info(self):
                return type("Mem", (), {"rss": 42 * 1024 * 1024})()

            def cpu_percent(self, interval):
                return 0.5

        monkeypatch.setattr(status_module.psutil, "Process", lambda pid: FakeProcess())
        monkeypatch.setattr(status_module.psutil, "getloadavg", lambda: (0.1, 0.2, 0.3))
        monkeypatch.setattr(status_module.psutil, "cpu_count", lambda: 8)

        await bot._cmd_status("admin@conference.example.org")
        body = last_body(bot)

        assert "RTBL Publish: enabled" in body
        assert "Sanity Check: ✅ OK" in body
        assert "Service:     pubsub.example.org" in body
    finally:
        await bot.db.close()


@pytest.mark.asyncio
async def test_all_mode_disables_paging_for_audit_banlists_and_bansearch(temp_db_path):
    bot = AuditStatusQueryBot()
    await bot.setup_db()
    try:
        await bot.db.execute(
            """
            CREATE TABLE rtbl_domains (
                domain TEXT,
                service_jid TEXT,
                node TEXT,
                reason TEXT,
                created_at INTEGER DEFAULT 0
            )
            """
        )
        await bot.db.execute(
            """
            CREATE TABLE rtbl_hashes (
                hash TEXT,
                service_jid TEXT,
                node TEXT,
                reason TEXT,
                created_at INTEGER DEFAULT 0
            )
            """
        )
        await bot.db.commit()

        for idx in range(12):
            await bot.audit_event(
                "ban_applied",
                actor="admin@example.org",
                target_type="jid",
                target=f"user{idx}@example.org",
                jid=f"user{idx}@example.org",
                comment="all-mode",
            )
            await bot.upsert_ban_db(
                f"user{idx}@example.org",
                f"Nick{idx}",
                0,
                "admin@example.org",
                "all-mode",
            )
        await bot.load_bans_from_db()

        await bot.db.execute(
            "INSERT INTO rtbl_domains (domain, service_jid, node, reason) VALUES (?, ?, ?, ?)",
            ("all.example", "xmppbl.org", "spam_source_domains", "all-mode"),
        )
        await bot.db.execute(
            "INSERT INTO rtbl_hashes (hash, service_jid, node, reason) VALUES (?, ?, ?, ?)",
            ("b" * 64, "xmppbl.org", "muc_bans_sha256", "all-mode"),
        )
        await bot.db.commit()

        await bot.cmd_audit(["all"], "admin@conference.example.org")
        body = last_body(bot)
        assert "Audit log (12) - All" in body
        assert "Page" not in body
        assert "user0@example.org" in body
        assert "user11@example.org" in body

        await bot.cmd_banlist("admin@conference.example.org", show_all=True)
        body = last_body(bot)
        assert "Banlist (12) - All" in body
        assert "Page" not in body
        assert "user0@example.org" in body
        assert "user11@example.org" in body

        await bot.cmd_banlist_rtbl("admin@conference.example.org", show_all=True)
        body = last_body(bot)
        assert "RTBL Banlist (2) - All" in body
        assert "Page" not in body
        assert "all.example" in body
        assert "bbbbbbbbbbbbbbbb" in body

        await bot.cmd_bansearch("all-mode", show_all=True)
        body = last_body(bot)
        assert "Bansearch 'all-mode'" in body
        assert "- All" in body
        assert "Page" not in body
        assert "user0@example.org" in body
        assert "user11@example.org" in body
    finally:
        await bot.db.close()


@pytest.mark.asyncio
async def test_reloadconfig_reports_changes_warnings_errors_and_exceptions():
    bot = AuditStatusQueryBot()
    bot._format_config_validation = lambda errors, warnings: "\n".join([*(f"❌ {e}" for e in errors), *(f"⚠️ {w}" for w in warnings)])

    async def reload_success():
        return ["COMMAND_PREFIX: ! -> ."], ["invalid value"], ["restart required"]

    bot.reload_runtime_config = reload_success
    await bot._cmd_reloadconfig("admin@conference.example.org")
    assert "Config reload aborted" in last_body(bot)
    assert "invalid value" in last_body(bot)

    async def reload_with_changes():
        return ["COMMAND_PREFIX: ! -> ."], [], ["restart required"]

    bot.reload_runtime_config = reload_with_changes
    await bot._cmd_reloadconfig("admin@conference.example.org")
    assert "Config reloaded successfully" in last_body(bot)
    assert "Warnings" in last_body(bot)
    assert "COMMAND_PREFIX" in last_body(bot)

    async def reload_no_changes():
        return [], [], []

    bot.reload_runtime_config = reload_no_changes
    await bot._cmd_reloadconfig("admin@conference.example.org")
    assert "No runtime config changes detected" in last_body(bot)

    async def reload_raises():
        raise RuntimeError("boom")

    bot.reload_runtime_config = reload_raises
    await bot._cmd_reloadconfig("admin@conference.example.org")
    assert "Failed to reload config: boom" in last_body(bot)


@pytest.mark.asyncio
async def test_config_output_includes_omemo_and_rtbl_publish_details():
    bot = AuditStatusQueryBot()
    bot.omemo_enabled = True
    bot.omemo_auto_encrypt_admin_room = True
    bot.omemo_plaintext_fallback = False
    bot.rtbl_publish_enabled = True
    bot.rtbl_publish_service = "pubsub.example.org"
    bot.rtbl_publish_jid_node = "muc_bans_sha256"
    bot.rtbl_publish_domain_node = "muc_bans_domains"

    async def fake_get_public_policy():
        return False, ""

    bot.get_public_policy = fake_get_public_policy

    await bot._cmd_config("admin@conference.example.org")
    body = last_body(bot)

    assert "Reply mode: follows incoming command encryption" in body
    assert "Auto-encrypt admin room: True" in body
    assert "Plaintext fallback: False" in body
    assert "Service:     pubsub.example.org" in body
    assert "JID node:    muc_bans_sha256" in body
    assert "Domain node: muc_bans_domains" in body
