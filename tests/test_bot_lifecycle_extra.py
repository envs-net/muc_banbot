"""Additional BanBot lifecycle coverage."""

from __future__ import annotations

import asyncio
import logging

import pytest

pytest.importorskip("aiosqlite")
pytest.importorskip("slixmpp")

from banbot import bot as bot_module




def test_slixmpp_statuses_warning_filter_suppresses_known_noise():
    status_record = logging.LogRecord(
        name="root",
        level=logging.WARNING,
        pathname=__file__,
        lineno=1,
        msg="Unknown stanza interface: statuses",
        args=(),
        exc_info=None,
    )
    other_record = logging.LogRecord(
        name="root",
        level=logging.WARNING,
        pathname=__file__,
        lineno=1,
        msg="Unknown stanza interface: other",
        args=(),
        exc_info=None,
    )

    log_filter = bot_module._SlixmppStatusesWarningFilter()

    assert log_filter.filter(status_record) is False
    assert log_filter.filter(other_record) is True

class FakeMucPlugin:
    """Minimal fake MUC plugin that records room joins requested by the bot."""

    def __init__(self):
        self.joined = []

    def join_muc(self, room, nick):
        self.joined.append((room, nick))


def _install_successful_join_stub(bot):
    """Make startup join tests deterministic without a live MUC service."""

    async def ensure_muc_joined(room, **_kwargs):
        bot.plugin["xep_0045"].join_muc(room, bot_module.NICK)
        bot.occupants[room] = {
            bot_module.NICK: {
                "jid": str(bot.boundjid.bare),
                "affiliation": "owner",
                "role": "moderator",
            }
        }
        bot.room_bot_nicks[room] = bot_module.NICK
        return True

    bot.ensure_muc_joined = ensure_muc_joined


class FakeTask:
    """Cancellable awaitable test double used to verify task shutdown behavior."""

    def __init__(self, name="task"):
        self.name = name
        self.cancelled = False
        self.awaited = False

    def done(self):
        return False

    def cancel(self):
        self.cancelled = True

    def __await__(self):
        async def _wait():
            self.awaited = True
            raise asyncio.CancelledError()

        return _wait().__await__()


class CompletedTask:
    """Simple task-like object returned by fake create_task in startup tests."""

    def __init__(self, name="task"):
        self.name = name
        self.cancelled = False

    def done(self):
        return False

    def cancel(self):
        self.cancelled = True


def _patch_lightweight_init(monkeypatch):
    def fake_register_plugin(self, name, *args, **kwargs):
        self.registered_plugins.append(name)

    def fake_add_event_handler(self, name, handler, *args, **kwargs):
        self.registered_events.append(name)

    def fake_apply_runtime_config(self):
        self.runtime_config_applied = True

    def fake_configure_omemo(self):
        self.omemo_configured = True

    monkeypatch.setattr(bot_module.BanBot, "register_plugin", fake_register_plugin)
    monkeypatch.setattr(bot_module.BanBot, "add_event_handler", fake_add_event_handler)
    monkeypatch.setattr(bot_module.BanBot, "apply_runtime_config", fake_apply_runtime_config)
    monkeypatch.setattr(bot_module.BanBot, "configure_omemo", fake_configure_omemo)

    original_init = bot_module.ClientXMPP.__init__

    def wrapped_init(self, *args, **kwargs):
        self.registered_plugins = []
        self.registered_events = []
        return original_init(self, *args, **kwargs)

    monkeypatch.setattr(bot_module.ClientXMPP, "__init__", wrapped_init)


def test_banbot_init_sets_runtime_state_and_registers_plugins(monkeypatch):
    _patch_lightweight_init(monkeypatch)

    bot = bot_module.BanBot("bot@example.org", "secret", "tests")

    assert str(bot.boundjid).startswith("bot@example.org/tests")
    assert bot.db is None
    assert bot.ban_cache == {}
    assert bot.protected_rooms == set()
    assert bot.registered_rooms == set()
    assert bot.command_prefix == "!"
    assert bot.runtime_config_applied is True
    assert bot.omemo_configured is True
    assert bot.registered_plugins[-7:] == [
        "xep_0030",
        "xep_0045",
        "xep_0054",
        "xep_0060",
        "xep_0084",
        "xep_0153",
        "xep_0249",
    ]
    assert "session_start" in bot.registered_events
    assert bot.registered_events.count("message") == 2
    assert bot.registered_events.count("groupchat_message") == 1
    incoming_filters = bot._XMLStream__filters["in"]
    assert any(
        getattr(handler, "__self__", None) is bot
        and getattr(handler, "__func__", None)
        is bot_module.RedactionMixin._redaction_incoming_filter
        for handler in incoming_filters
    )
    assert "groupchat_presence" in bot.registered_events
    assert "disconnected" in bot.registered_events
    assert "connection_failed" in bot.registered_events


@pytest.mark.asyncio
async def test_stop_background_tasks_cancels_running_tasks(monkeypatch):
    _patch_lightweight_init(monkeypatch)
    bot = bot_module.BanBot("bot@example.org", "secret")
    unban = FakeTask("unban")
    health = FakeTask("health")
    version = FakeTask("version")
    redaction_cleanup = FakeTask("redaction_cleanup")
    bot.unban_task = unban
    bot.health_check_task = health
    bot.version_check_task = version
    bot.redaction_cleanup_task = redaction_cleanup

    await bot.stop_background_tasks()

    assert unban.cancelled is True
    assert health.cancelled is True
    assert version.cancelled is True
    assert redaction_cleanup.cancelled is True
    assert unban.awaited is True
    assert health.awaited is True
    assert version.awaited is True
    assert redaction_cleanup.awaited is True


@pytest.mark.asyncio
async def test_start_runs_startup_flow_and_registers_room_handlers(monkeypatch):
    _patch_lightweight_init(monkeypatch)
    bot = bot_module.BanBot("bot@example.org", "secret")
    bot.protected_rooms = {"room1@conference.example.org", "room2@conference.example.org"}
    bot.registered_rooms = set()
    bot.plugin = {"xep_0045": FakeMucPlugin()}
    _install_successful_join_stub(bot)
    bot.sent = []
    bot.version_check_enabled = True
    bot.version_check_url = "https://github.com/envs-net/muc_banbot/releases/latest"
    bot.redaction_enabled = False
    bot.announce_startup = True
    bot.reconnecting = False

    calls = []

    async def record(name, result=None):
        calls.append(name)
        return result

    bot.setup_db = lambda: record("setup_db")
    bot.load_bans_from_db = lambda: record("load_bans_from_db")
    bot.cleanup_old_audit_logs = lambda: record("cleanup_old_audit_logs", 0)
    bot.setup_ignorelist = lambda: record("setup_ignorelist")
    bot.get_roster = lambda: record("get_roster")
    bot.wait_for_occupants = lambda timeout=20: record(f"wait_for_occupants:{timeout}")
    bot.check_bot_admin_rights = lambda: record("check_bot_admin_rights")
    bot.sync_admins = lambda announce=False: record(f"sync_admins:{announce}")
    bot.sync_bans_startup = lambda: record("sync_bans_startup")
    bot.setup_rtbl = lambda: record("setup_rtbl")
    bot.setup_rtbl_publish = lambda: record("setup_rtbl_publish")
    bot.update_vcard = lambda: record("update_vcard")
    bot.bot_send_message = lambda **kwargs: record("bot_send_message", bot.sent.append(kwargs))
    bot.send_presence = lambda: calls.append("send_presence")

    async def never_running_worker():
        await asyncio.sleep(999)

    bot._rtbl_refresh_worker = never_running_worker
    bot.unban_worker = never_running_worker
    bot.health_check_worker = never_running_worker
    bot.version_check_worker = never_running_worker

    async def no_sleep(_delay):
        return None

    created_tasks = []

    def fake_create_task(coro):
        created_tasks.append(coro)
        coro.close()
        return CompletedTask(f"task-{len(created_tasks)}")

    monkeypatch.setattr(bot_module.asyncio, "sleep", no_sleep)
    monkeypatch.setattr(bot_module.asyncio, "create_task", fake_create_task)

    await bot.start(None)

    assert "setup_db" in calls
    assert "load_bans_from_db" in calls
    assert "setup_ignorelist" in calls
    assert "wait_for_occupants:6" in calls
    assert "sync_admins:False" in calls
    assert "setup_rtbl_publish" in calls
    assert "update_vcard" in calls
    assert bot.plugin["xep_0045"].joined == [
        (bot_module.ADMIN_ROOM, bot_module.NICK),
        ("room1@conference.example.org", bot_module.NICK),
        ("room2@conference.example.org", bot_module.NICK),
    ] or sorted(bot.plugin["xep_0045"].joined) == sorted([
        (bot_module.ADMIN_ROOM, bot_module.NICK),
        ("room1@conference.example.org", bot_module.NICK),
        ("room2@conference.example.org", bot_module.NICK),
    ])
    assert bot_module.ADMIN_ROOM in bot.registered_rooms
    assert "room1@conference.example.org" in bot.registered_rooms
    assert "room2@conference.example.org" in bot.registered_rooms
    assert len(created_tasks) == 4
    assert bot.unban_task is not None
    assert bot.health_check_task is not None
    assert bot.version_check_task is not None
    assert bot.reconnecting is False
    assert bot.sent
    assert "Bot has restarted" in bot.sent[-1]["mbody"]


@pytest.mark.asyncio
async def test_start_runs_redaction_cleanup_and_worker_when_enabled(monkeypatch):
    _patch_lightweight_init(monkeypatch)
    bot = bot_module.BanBot("bot@example.org", "secret")
    bot.protected_rooms = set()
    bot.registered_rooms = set()
    bot.plugin = {"xep_0045": FakeMucPlugin()}
    _install_successful_join_stub(bot)
    bot.sent = []
    bot.version_check_enabled = False
    bot.version_check_url = None
    bot.redaction_enabled = True
    bot.announce_startup = False
    bot.reconnecting = False

    calls = []

    async def record(name, result=None):
        calls.append(name)
        return result

    bot.setup_db = lambda: record("setup_db")
    bot.run_redaction_cleanup_automatic = lambda actor="system": record(f"redaction_cleanup:{actor}")
    bot.load_pending_room_invites = lambda: record("load_pending_room_invites")
    bot.load_bans_from_db = lambda: record("load_bans_from_db")
    bot.cleanup_old_audit_logs = lambda: record("cleanup_old_audit_logs", 0)
    bot.setup_ignorelist = lambda: record("setup_ignorelist")
    bot.get_roster = lambda: record("get_roster")
    bot.wait_for_occupants = lambda timeout=20: record(f"wait_for_occupants:{timeout}")
    bot.check_bot_admin_rights = lambda: record("check_bot_admin_rights")
    bot.sync_admins = lambda announce=False: record(f"sync_admins:{announce}")
    bot.sync_bans_startup = lambda: record("sync_bans_startup")
    bot.setup_rtbl = lambda: record("setup_rtbl")
    bot.setup_rtbl_publish = lambda: record("setup_rtbl_publish")
    bot.update_vcard = lambda: record("update_vcard")
    bot.bot_send_message = lambda **kwargs: record("bot_send_message", bot.sent.append(kwargs))
    bot.send_presence = lambda: calls.append("send_presence")

    async def never_running_worker():
        await asyncio.sleep(999)

    bot._rtbl_refresh_worker = never_running_worker
    bot.unban_worker = never_running_worker
    bot.health_check_worker = never_running_worker
    bot.version_check_worker = never_running_worker
    bot.redaction_cleanup_worker = never_running_worker

    async def no_sleep(_delay):
        return None

    created_tasks = []

    def fake_create_task(coro):
        created_tasks.append(coro)
        coro.close()
        return CompletedTask(f"task-{len(created_tasks)}")

    monkeypatch.setattr(bot_module.asyncio, "sleep", no_sleep)
    monkeypatch.setattr(bot_module.asyncio, "create_task", fake_create_task)

    await bot.start(None)

    assert calls.index("setup_db") < calls.index("redaction_cleanup:system")
    assert "redaction_cleanup:system" in calls
    assert len(created_tasks) == 4
    assert bot.redaction_cleanup_task is not None


@pytest.mark.asyncio
async def test_start_announces_reconnect_differently_from_restart(monkeypatch):
    _patch_lightweight_init(monkeypatch)
    bot = bot_module.BanBot("bot@example.org", "secret")
    bot.protected_rooms = set()
    bot.registered_rooms = set()
    bot.plugin = {"xep_0045": FakeMucPlugin()}
    _install_successful_join_stub(bot)
    bot.sent = []
    bot.version_check_enabled = False
    bot.version_check_url = None
    bot.redaction_enabled = False
    bot.announce_startup = True
    bot.reconnecting = True

    calls = []

    async def record(name, result=None):
        calls.append(name)
        return result

    bot.setup_db = lambda: record("setup_db")
    bot.load_bans_from_db = lambda: record("load_bans_from_db")
    bot.cleanup_old_audit_logs = lambda: record("cleanup_old_audit_logs", 0)
    bot.setup_ignorelist = lambda: record("setup_ignorelist")
    bot.get_roster = lambda: record("get_roster")
    bot.wait_for_occupants = lambda timeout=20: record(f"wait_for_occupants:{timeout}")
    bot.check_bot_admin_rights = lambda: record("check_bot_admin_rights")
    bot.sync_admins = lambda announce=False: record(f"sync_admins:{announce}")
    bot.sync_bans_startup = lambda: record("sync_bans_startup")
    bot.setup_rtbl = lambda: record("setup_rtbl")
    bot.setup_rtbl_publish = lambda: record("setup_rtbl_publish")
    bot.update_vcard = lambda: record("update_vcard")
    bot.bot_send_message = lambda **kwargs: record("bot_send_message", bot.sent.append(kwargs))
    bot.send_presence = lambda: calls.append("send_presence")

    async def never_running_worker():
        await asyncio.sleep(999)

    bot._rtbl_refresh_worker = never_running_worker
    bot.unban_worker = never_running_worker
    bot.health_check_worker = never_running_worker
    bot.version_check_worker = never_running_worker

    async def no_sleep(_delay):
        return None

    created_tasks = []

    def fake_create_task(coro):
        created_tasks.append(coro)
        coro.close()
        return CompletedTask(f"task-{len(created_tasks)}")

    monkeypatch.setattr(bot_module.asyncio, "sleep", no_sleep)
    monkeypatch.setattr(bot_module.asyncio, "create_task", fake_create_task)

    await bot.start(None)

    assert bot.reconnecting is False
    assert bot.last_reconnect_time is not None
    assert bot.sent
    assert "Bot has reconnected" in bot.sent[-1]["mbody"]
    assert "Bot has restarted" not in bot.sent[-1]["mbody"]


def test_connect_xmpp_uses_configured_address_and_direct_tls(monkeypatch):
    class FakeBoundJid:
        host = "jid-domain.example.org"

    class FakeXMPP:
        boundjid = FakeBoundJid()

        def __init__(self):
            self.connect_kwargs = None

        def connect(self, address=None, use_ssl=False, force_starttls=True):
            self.connect_kwargs = {
                "address": address,
                "use_ssl": use_ssl,
                "force_starttls": force_starttls,
            }
            return True

    monkeypatch.setattr(bot_module.config, "CONNECT_HOST", "xmpp.example.org", raising=False)
    monkeypatch.setattr(bot_module.config, "CONNECT_PORT", 5223, raising=False)
    monkeypatch.setattr(bot_module.config, "CONNECT_DIRECT_TLS", True, raising=False)

    xmpp = FakeXMPP()

    assert bot_module.connect_xmpp(xmpp) is True
    assert xmpp.connect_kwargs == {
        "address": ("xmpp.example.org", 5223),
        "use_ssl": True,
        "force_starttls": False,
    }


def test_connect_xmpp_keeps_older_connect_signatures_compatible(monkeypatch):
    class FakeBoundJid:
        host = "jid-domain.example.org"

    class FakeXMPP:
        boundjid = FakeBoundJid()

        def __init__(self):
            self.called = False

        def connect(self):
            self.called = True
            return False

    monkeypatch.setattr(bot_module.config, "CONNECT_HOST", None, raising=False)
    monkeypatch.setattr(bot_module.config, "CONNECT_PORT", 443, raising=False)
    monkeypatch.setattr(bot_module.config, "CONNECT_DIRECT_TLS", True, raising=False)

    xmpp = FakeXMPP()

    assert bot_module.connect_xmpp(xmpp) is False
    assert xmpp.called is True


def test_main_exits_when_connect_fails(monkeypatch):
    class FakeLoop:
        def run_forever(self):  # pragma: no cover - should not be reached
            raise AssertionError("run_forever should not be called")

    class FakeBanBot:
        def __init__(self, jid, password, resource):
            self.jid = jid
            self.password = password
            self.resource = resource
            self.loop = FakeLoop()
            self.db = None
            self.connected = False

        def _validate_config(self):
            return ["bad setting"], ["restart required"]

        def _format_config_validation(self, errors, warnings):
            return "❌ bad setting\n⚠️ restart required"

        def connect(self):
            self.connected = True
            return False

    monkeypatch.setattr(bot_module, "BanBot", FakeBanBot)
    monkeypatch.setattr(bot_module, "get_config_resource", lambda: "tests")

    with pytest.raises(SystemExit) as excinfo:
        bot_module.main()

    assert excinfo.value.code == 1


def test_main_exits_when_bot_initialization_fails(monkeypatch):
    class FailingBanBot:
        def __init__(self, jid, password, resource):
            raise RuntimeError("config exploded")

    monkeypatch.setattr(bot_module, "BanBot", FailingBanBot)
    monkeypatch.setattr(bot_module, "get_config_resource", lambda: "tests")

    with pytest.raises(SystemExit) as excinfo:
        bot_module.main()

    assert excinfo.value.code == 1


def test_main_exits_when_event_loop_stops_unexpectedly(monkeypatch):
    class FakeLoop:
        def run_forever(self):
            return None

    class FakeBanBot:
        def __init__(self, jid, password, resource):
            self.loop = FakeLoop()
            self.db = None

        def _validate_config(self):
            return [], []

        def _format_config_validation(self, errors, warnings):
            return "✅ Config validation passed"

        def connect(self):
            return True

    monkeypatch.setattr(bot_module, "BanBot", FakeBanBot)
    monkeypatch.setattr(bot_module, "get_config_resource", lambda: "tests")

    with pytest.raises(SystemExit) as excinfo:
        bot_module.main()

    assert excinfo.value.code == 1


def test_main_closes_db_and_disconnects_on_keyboard_interrupt(monkeypatch):
    events = []

    class FakeDb:
        async def close(self):
            events.append("db.close")

    class FakeLoop:
        def run_forever(self):
            events.append("run_forever")
            raise KeyboardInterrupt()

        def run_until_complete(self, coro):
            events.append("run_until_complete")
            try:
                coro.send(None)
            except StopIteration:
                return None

    class FakeBanBot:
        def __init__(self, jid, password, resource):
            self.loop = FakeLoop()
            self.db = FakeDb()

        def _validate_config(self):
            return [], []

        def _format_config_validation(self, errors, warnings):
            return "✅ Config validation passed"

        def connect(self):
            return True

        def disconnect(self):
            events.append("disconnect")

    monkeypatch.setattr(bot_module, "BanBot", FakeBanBot)
    monkeypatch.setattr(bot_module, "get_config_resource", lambda: "tests")

    assert bot_module.main() is None
    assert events == ["run_forever", "run_until_complete", "db.close", "disconnect"]
