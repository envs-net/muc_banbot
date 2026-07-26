"""Additional room, health-check and update helper tests."""

from __future__ import annotations

import importlib
import asyncio
import time
from xml.etree import ElementTree as ET

import pytest

aiosqlite = pytest.importorskip("aiosqlite")

from banbot.db import DatabaseMixin
from banbot.health_check import HealthCheckMixin
from banbot.rooms import RoomMixin
from banbot.rooms.invites import RoomInviteMixin
from banbot.utils import bare_jid
from banbot.updates import UpdateMixin


class FakeDisco:
    def __init__(self, identities):
        self.identities = identities

    async def get_info(self, jid, timeout=5):
        return {"disco_info": {"identities": self.identities}}


class FakeMucPlugin:
    def __init__(self):
        self.joined = []
        self.left = []

    def join_muc(self, room, nick):
        self.joined.append((room, nick))

    def leave_muc(self, room, nick):
        self.left.append((room, nick))


class RoomHealthBot(DatabaseMixin, RoomMixin, RoomInviteMixin, HealthCheckMixin, UpdateMixin):
    def __init__(self):
        self.protected_rooms = {"room@conference.example.test"}
        self.registered_rooms = set()
        self.init_room_invite_state()
        self.event_handlers = []
        self.occupants = {"room@conference.example.test": {"BanBot": {"jid": "bot@example.org"}}}
        self.plugin = {
            "xep_0030": FakeDisco([("conference", "text", "Room")]),
            "xep_0045": FakeMucPlugin(),
        }
        self.sent = []
        self.command_prefix = "!"
        self.room_invites_enabled = True
        self.rtbl_enabled = False
        self.health_check_interval = 0
        self.last_audit_cleanup_run = 0
        self.cleanup_calls = 0
        self.admin_ok = True
        self.version_check_enabled = True
        self.version_check_url = "https://github.com/envs-net/muc_banbot/releases/latest"
        self.last_version_check_result = None
        self.last_update_notified_version = None
        self.version_check_interval = 3600

    async def bot_send_message(self, **kwargs):
        self.sent.append(kwargs)

    def bare_jid(self, jid):
        return bare_jid(jid)

    def add_event_handler(self, name, handler):
        self.event_handlers.append((name, handler))

    async def muc_online(self, *args, **kwargs):
        pass

    async def muc_offline(self, *args, **kwargs):
        pass

    async def sync_bans_to_rooms_for_single_room(self, room):
        self.synced_room = room

    async def check_jid_against_rtbl(self, jid, nick):
        self.rtbl_checked = (jid, nick)

    async def cleanup_old_audit_logs(self):
        self.cleanup_calls += 1
        self.last_audit_cleanup_run = time.time()
        return 0

    def is_bot_admin_or_owner(self, room):
        return self.admin_ok


@pytest.mark.asyncio
async def test_validate_room_jid_rejects_bad_format_and_accepts_muc(temp_db_path):
    bot = RoomHealthBot()
    ok, error = await bot.validate_room_jid("not-a-jid")
    assert ok is False
    assert "Invalid JID format" in error

    ok, error = await bot.validate_room_jid("TestRoom@Conference.Example.Test")
    assert ok is True
    assert error == ""


@pytest.mark.asyncio
async def test_room_list_add_and_remove_flow(temp_db_path, monkeypatch):
    rooms_module = importlib.import_module("banbot.rooms")

    monkeypatch.setattr(rooms_module, "ADMIN_ROOM", "admin@conference.example.org")
    monkeypatch.setattr(rooms_module, "NICK", "BanBot")
    bot = RoomHealthBot()
    bot.protected_rooms = set()
    bot.occupants["new@conference.example.test"] = {"BanBot": {"jid": "bot@example.org"}}
    await bot.setup_db()
    try:
        await bot.cmd_room(["list"], "admin@conference.example.org")
        assert "No protected rooms" in bot.sent[-1]["mbody"]

        await bot.cmd_room(["add", "new@conference.example.test"], "admin@conference.example.org")
        assert "new@conference.example.test" in bot.protected_rooms
        assert bot.plugin["xep_0045"].joined
        assert "Room added" in bot.sent[-1]["mbody"]

        await bot.cmd_room(["delete", "new@conference.example.test"], "admin@conference.example.org")
        assert "new@conference.example.test" not in bot.protected_rooms
        assert bot.plugin["xep_0045"].left
    finally:
        await bot.db.close()


@pytest.mark.asyncio
async def test_health_check_reports_missing_bot_and_lost_admin(monkeypatch):
    bot = RoomHealthBot()
    bot.occupants = {"room@conference.example.test": {}}
    bot.admin_ok = False

    calls = {"count": 0}

    async def fake_sleep(delay):
        calls["count"] += 1
        if calls["count"] > 1:
            raise asyncio.CancelledError()

    monkeypatch.setattr("banbot.health_check.asyncio.sleep", fake_sleep)

    with pytest.raises(asyncio.CancelledError):
        await bot.health_check_worker()

    assert any("Bot not in room" in item["mbody"] for item in bot.sent)


@pytest.mark.asyncio
async def test_health_check_skips_while_reconnecting(monkeypatch):
    bot = RoomHealthBot()
    bot.reconnecting = True
    bot.occupants = {"room@conference.example.test": {}}

    calls = {"count": 0}

    async def fake_sleep(delay):
        calls["count"] += 1
        if calls["count"] > 1:
            raise asyncio.CancelledError()

    monkeypatch.setattr("banbot.health_check.asyncio.sleep", fake_sleep)

    with pytest.raises(asyncio.CancelledError):
        await bot.health_check_worker()

    assert bot.sent == []


@pytest.mark.asyncio
async def test_version_check_helpers_and_announcement(monkeypatch):
    bot = RoomHealthBot()
    assert bot._parse_version_tuple("v2.10.1") == (2, 10, 1)
    assert bot._is_remote_version_newer("2.2.0", "2.1.1") is True
    assert bot._github_api_url_from_release_url(
        "https://github.com/envs-net/muc_banbot/releases/latest"
    ) == "https://api.github.com/repos/envs-net/muc_banbot/releases/latest"

    monkeypatch.setattr(bot, "_fetch_latest_release_version_sync", lambda: "99.0.0")
    update_available, remote, error = await bot.check_for_updates_once(announce=True)
    assert update_available is True
    assert remote == "99.0.0"
    assert error is None
    assert "New bot version" in bot.sent[-1]["mbody"]


@pytest.mark.asyncio
async def test_room_list_all_disables_paging(temp_db_path, monkeypatch):
    rooms_module = importlib.import_module("banbot.rooms")

    monkeypatch.setattr(rooms_module, "ADMIN_ROOM", "admin@conference.example.org")
    monkeypatch.setattr(rooms_module, "NICK", "BanBot")
    bot = RoomHealthBot()
    bot.protected_rooms = {f"room{idx}@conference.example.test" for idx in range(12)}
    await bot.setup_db()
    try:
        await bot.cmd_room(["list", "all"], "admin@conference.example.org")
        body = bot.sent[-1]["mbody"]
        assert "Protected Rooms (12) - All" in body
        assert "Page" not in body
        assert "room0@conference.example.test" in body
        assert "room11@conference.example.test" in body
    finally:
        await bot.db.close()


def test_github_api_url_rejects_non_github_and_short_paths():
    bot = RoomHealthBot()
    assert bot._github_api_url_from_release_url("https://example.org/releases/latest") is None
    assert bot._github_api_url_from_release_url("https://github.com/only-owner") is None


@pytest.mark.asyncio
async def test_version_check_disabled_returns_explanatory_error():
    bot = RoomHealthBot()
    bot.version_check_enabled = False
    update_available, remote, error = await bot.check_for_updates_once()
    assert update_available is False
    assert remote is None
    assert "disabled" in error


@pytest.mark.asyncio
async def test_version_check_same_version_does_not_announce(monkeypatch):
    updates_module = importlib.import_module("banbot.updates")

    bot = RoomHealthBot()
    local = updates_module.__version__.lstrip("v").strip()
    monkeypatch.setattr(bot, "_fetch_latest_release_version_sync", lambda: local)

    update_available, remote, error = await bot.check_for_updates_once(announce=True)

    assert update_available is False
    assert remote == local
    assert error is None
    assert bot.sent == []


@pytest.mark.asyncio
async def test_version_check_error_is_returned(monkeypatch):
    bot = RoomHealthBot()

    def fail_fetch():
        raise RuntimeError("network down")

    monkeypatch.setattr(bot, "_fetch_latest_release_version_sync", fail_fetch)
    update_available, remote, error = await bot.check_for_updates_once(announce=True)

    assert update_available is False
    assert remote is None
    assert "network down" in error


def test_fetch_latest_release_uses_github_api(monkeypatch):
    updates_module = importlib.import_module("banbot.updates")

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return b'{"tag_name": "v9.8.7"}'

    seen = {}

    def fake_urlopen(req, timeout):
        seen["url"] = req.full_url
        seen["timeout"] = timeout
        return FakeResponse()

    bot = RoomHealthBot()
    monkeypatch.setattr(updates_module.urllib.request, "urlopen", fake_urlopen)

    assert bot._fetch_latest_release_version_via_github_api_sync() == "9.8.7"
    assert seen["url"] == "https://api.github.com/repos/envs-net/muc_banbot/releases/latest"
    assert seen["timeout"] == 15


def test_fetch_latest_release_falls_back_to_redirect(monkeypatch):
    updates_module = importlib.import_module("banbot.updates")

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def geturl(self):
            return "https://github.com/envs-net/muc_banbot/releases/tag/v7.6.5"

    def fake_urlopen(req, timeout):
        return FakeResponse()

    bot = RoomHealthBot()
    bot.version_check_url = "https://example.org/latest"
    monkeypatch.setattr(updates_module.urllib.request, "urlopen", fake_urlopen)

    assert bot._fetch_latest_release_version_via_redirect_sync() == "7.6.5"


def test_fetch_latest_release_reports_bad_redirect(monkeypatch):
    updates_module = importlib.import_module("banbot.updates")

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def geturl(self):
            return "https://github.com/envs-net/muc_banbot/releases"

    bot = RoomHealthBot()
    bot.version_check_url = "https://example.org/latest"
    monkeypatch.setattr(updates_module.urllib.request, "urlopen", lambda req, timeout: FakeResponse())

    with pytest.raises(ValueError, match="Unexpected release redirect URL"):
        bot._fetch_latest_release_version_via_redirect_sync()


@pytest.mark.asyncio
async def test_room_invite_creates_pending_invite_and_announces_admin_room(fake_msg_factory, monkeypatch):
    invite_module = importlib.import_module("banbot.rooms.invites")

    monkeypatch.setattr(invite_module, "ADMIN_ROOM", "admin@conference.example.org")
    bot = RoomHealthBot()
    message_xml = ET.Element("message")
    ET.SubElement(
        message_xml,
        "{jabber:x:conference}x",
        {"jid": "new@conference.example.test", "reason": "please protect this room"},
    )
    msg = fake_msg_factory(room="alice@example.org", full_from="alice@example.org/laptop", xml=message_xml)

    handled = await bot.handle_room_invite_message(msg)

    assert handled is True
    assert len(bot.pending_room_invites) == 1
    invite = bot.pending_room_invites[1]
    assert invite["room_jid"] == "new@conference.example.test"
    assert invite["inviter"] == "alice@example.org"
    body = bot.sent[-1]["mbody"]
    assert "New protected-room invite" in body
    assert "new@conference.example.test" in body
    assert "alice@\u200bexample.org" in body
    assert "!room invite accept 1" in body


@pytest.mark.asyncio
async def test_room_invite_deduplicates_same_room_and_inviter(fake_msg_factory, monkeypatch):
    invite_module = importlib.import_module("banbot.rooms.invites")

    monkeypatch.setattr(invite_module, "ADMIN_ROOM", "admin@conference.example.org")
    bot = RoomHealthBot()
    message_xml = ET.Element("message")
    ET.SubElement(message_xml, "{jabber:x:conference}x", {"jid": "new@conference.example.test"})
    msg = fake_msg_factory(room="alice@example.org", full_from="alice@example.org/laptop", xml=message_xml)

    await bot.handle_room_invite_message(msg)
    await bot.handle_room_invite_message(msg)

    assert len(bot.pending_room_invites) == 1
    assert len(bot.sent) == 1


@pytest.mark.asyncio
async def test_room_invite_same_room_from_different_inviter_gets_separate_entry(fake_msg_factory, monkeypatch):
    invite_module = importlib.import_module("banbot.rooms.invites")

    monkeypatch.setattr(invite_module, "ADMIN_ROOM", "admin@conference.example.org")
    bot = RoomHealthBot()
    xml_one = ET.Element("message")
    xml_two = ET.Element("message")
    ET.SubElement(xml_one, "{jabber:x:conference}x", {"jid": "new@conference.example.test"})
    ET.SubElement(xml_two, "{jabber:x:conference}x", {"jid": "new@conference.example.test"})

    await bot.handle_room_invite_message(fake_msg_factory(room="alice@example.org", xml=xml_one))
    await bot.handle_room_invite_message(fake_msg_factory(room="bob@example.org", xml=xml_two))

    assert len(bot.pending_room_invites) == 2


@pytest.mark.asyncio
async def test_room_invite_invalid_room_jid_is_reported(fake_msg_factory, monkeypatch):
    invite_module = importlib.import_module("banbot.rooms.invites")

    monkeypatch.setattr(invite_module, "ADMIN_ROOM", "admin@conference.example.org")
    bot = RoomHealthBot()
    message_xml = ET.Element("message")
    ET.SubElement(message_xml, "{jabber:x:conference}x", {"jid": "not-a-room"})
    msg = fake_msg_factory(room="alice@example.org", xml=message_xml)

    await bot.handle_room_invite_message(msg)

    assert bot.pending_room_invites == {}
    assert "Ignored invalid protected-room invite" in bot.sent[-1]["mbody"]


@pytest.mark.asyncio
async def test_room_invite_service_disabled_consumes_invite_without_admin_spam(fake_msg_factory):
    bot = RoomHealthBot()
    bot.room_invites_enabled = False
    message_xml = ET.Element("message")
    ET.SubElement(message_xml, "{jabber:x:conference}x", {"jid": "new@conference.example.test"})
    msg = fake_msg_factory(room="alice@example.org", xml=message_xml)

    handled = await bot.handle_room_invite_message(msg)

    assert handled is True
    assert bot.pending_room_invites == {}
    assert bot.sent == []


@pytest.mark.asyncio
async def test_room_invite_list_supports_all_last_and_paging(monkeypatch):
    invite_module = importlib.import_module("banbot.rooms.invites")

    monkeypatch.setattr(invite_module, "ADMIN_ROOM", "admin@conference.example.org")
    bot = RoomHealthBot()
    bot.pending_room_invites = {
        idx: {"id": idx, "room_jid": f"room{idx}@conference.example.test", "inviter": "alice@example.org", "reason": "", "created_at": 0}
        for idx in range(1, 13)
    }
    bot.pending_room_invite_index = {
        (str(invite["room_jid"]), str(invite["inviter"])): idx
        for idx, invite in bot.pending_room_invites.items()
    }

    await bot.cmd_room_invite(["list", "all"], "admin@conference.example.org")
    assert "Pending Room Invites (12) - All" in bot.sent[-1]["mbody"]
    assert "room12@conference.example.test" in bot.sent[-1]["mbody"]

    await bot.cmd_room_invite(["list", "last"], "admin@conference.example.org")
    body = bot.sent[-1]["mbody"]
    assert "Page 2/2" in body
    assert "room11@conference.example.test" in body


@pytest.mark.asyncio
async def test_room_invite_accept_uses_existing_room_add_flow(temp_db_path, monkeypatch):
    invite_module = importlib.import_module("banbot.rooms.invites")
    rooms_module = importlib.import_module("banbot.rooms")

    monkeypatch.setattr(invite_module, "ADMIN_ROOM", "admin@conference.example.org")
    monkeypatch.setattr(rooms_module, "ADMIN_ROOM", "admin@conference.example.org")
    monkeypatch.setattr(rooms_module, "NICK", "BanBot")
    bot = RoomHealthBot()
    bot.protected_rooms = set()
    bot.occupants["new@conference.example.test"] = {"BanBot": {"jid": "bot@example.org"}}
    bot.pending_room_invites = {
        1: {"id": 1, "room_jid": "new@conference.example.test", "inviter": "alice@example.org", "reason": "", "created_at": 0}
    }
    bot.pending_room_invite_index = {("new@conference.example.test", "alice@example.org"): 1}
    await bot.setup_db()
    try:
        await bot.cmd_room_invite(["accept", "1"], "admin@conference.example.org")
        assert "new@conference.example.test" in bot.protected_rooms
        assert bot.pending_room_invites == {}
        assert bot.plugin["xep_0045"].joined
    finally:
        await bot.db.close()


@pytest.mark.asyncio
async def test_room_invite_decline_removes_pending_invite(monkeypatch):
    invite_module = importlib.import_module("banbot.rooms.invites")

    monkeypatch.setattr(invite_module, "ADMIN_ROOM", "admin@conference.example.org")
    bot = RoomHealthBot()
    bot.pending_room_invites = {
        1: {"id": 1, "room_jid": "new@conference.example.test", "inviter": "alice@example.org", "reason": "", "created_at": 0}
    }
    bot.pending_room_invite_index = {("new@conference.example.test", "alice@example.org"): 1}

    await bot.cmd_room_invite(["decline", "1"], "admin@conference.example.org")

    assert bot.pending_room_invites == {}
    assert "Declined protected-room invite #1" in bot.sent[-1]["mbody"]


@pytest.mark.asyncio
async def test_room_invite_remove_alias_declines_pending_invite(monkeypatch):
    invite_module = importlib.import_module("banbot.rooms.invites")

    monkeypatch.setattr(invite_module, "ADMIN_ROOM", "admin@conference.example.org")
    bot = RoomHealthBot()
    bot.pending_room_invites = {
        1: {"id": 1, "room_jid": "new@conference.example.test", "inviter": "alice@example.org", "reason": "", "created_at": 0}
    }
    bot.pending_room_invite_index = {("new@conference.example.test", "alice@example.org"): 1}

    await bot.cmd_room_invite(["remove", "1"], "admin@conference.example.org")

    assert bot.pending_room_invites == {}
    assert "Declined protected-room invite #1" in bot.sent[-1]["mbody"]


@pytest.mark.asyncio
async def test_mediated_room_invite_is_detected(fake_msg_factory, monkeypatch):
    invite_module = importlib.import_module("banbot.rooms.invites")

    monkeypatch.setattr(invite_module, "ADMIN_ROOM", "admin@conference.example.org")
    bot = RoomHealthBot()
    message_xml = ET.Element("message")
    x_el = ET.SubElement(message_xml, "{http://jabber.org/protocol/muc#user}x")
    invite_el = ET.SubElement(x_el, "{http://jabber.org/protocol/muc#user}invite", {"from": "alice@example.org/laptop"})
    reason_el = ET.SubElement(invite_el, "{http://jabber.org/protocol/muc#user}reason")
    reason_el.text = "please add"
    msg = fake_msg_factory(room="mediated@conference.example.test", full_from="mediated@conference.example.test", xml=message_xml)

    await bot.handle_room_invite_message(msg)

    assert bot.pending_room_invites[1]["room_jid"] == "mediated@conference.example.test"
    assert bot.pending_room_invites[1]["inviter"] == "alice@example.org"
    assert bot.pending_room_invites[1]["reason"] == "please add"


@pytest.mark.asyncio
async def test_room_invite_command_reports_disabled_service():
    bot = RoomHealthBot()
    bot.room_invites_enabled = False

    await bot.cmd_room_invite(["list"], "admin@conference.example.org")

    assert "Room invite service is disabled" in bot.sent[-1]["mbody"]


@pytest.mark.asyncio
async def test_room_invite_persists_and_loads_after_restart(temp_db_path, fake_msg_factory, monkeypatch):
    invite_module = importlib.import_module("banbot.rooms.invites")

    monkeypatch.setattr(invite_module, "ADMIN_ROOM", "admin@conference.example.org")

    bot = RoomHealthBot()
    await bot.setup_db()
    try:
        message_xml = ET.Element("message")
        ET.SubElement(
            message_xml,
            "{jabber:x:conference}x",
            {"jid": "persistent@conference.example.test", "reason": "please add"},
        )
        msg = fake_msg_factory(
            room="alice@example.org",
            full_from="alice@example.org/laptop",
            xml=message_xml,
        )

        await bot.handle_room_invite_message(msg)

        assert len(bot.pending_room_invites) == 1
        invite_id = next(iter(bot.pending_room_invites))
    finally:
        await bot.db.close()

    restarted = RoomHealthBot()
    await restarted.setup_db()
    try:
        await restarted.load_pending_room_invites()

        assert invite_id in restarted.pending_room_invites
        invite = restarted.pending_room_invites[invite_id]
        assert invite["room_jid"] == "persistent@conference.example.test"
        assert invite["inviter"] == "alice@example.org"
        assert invite["reason"] == "please add"
    finally:
        await restarted.db.close()


@pytest.mark.asyncio
async def test_room_invite_cleanup_clears_db_and_cache(temp_db_path, fake_msg_factory, monkeypatch):
    invite_module = importlib.import_module("banbot.rooms.invites")

    monkeypatch.setattr(invite_module, "ADMIN_ROOM", "admin@conference.example.org")

    bot = RoomHealthBot()
    await bot.setup_db()
    try:
        message_xml = ET.Element("message")
        ET.SubElement(
            message_xml,
            "{jabber:x:conference}x",
            {"jid": "cleanup@conference.example.test"},
        )
        msg = fake_msg_factory(
            room="alice@example.org",
            full_from="alice@example.org/laptop",
            xml=message_xml,
        )

        await bot.handle_room_invite_message(msg)
        assert bot.pending_room_invites

        await bot.cmd_room_invite(["cleanup"], "admin@conference.example.org")

        assert bot.pending_room_invites == {}
        assert bot.pending_room_invite_index == {}
        assert "Deleted pending invites: 1" in bot.sent[-1]["mbody"]

        async with bot.db.execute("SELECT COUNT(*) FROM room_invites") as cursor:
            row = await cursor.fetchone()
        assert row[0] == 0
    finally:
        await bot.db.close()


@pytest.mark.asyncio
async def test_room_invite_accept_removes_persisted_invite(temp_db_path, monkeypatch):
    invite_module = importlib.import_module("banbot.rooms.invites")
    rooms_module = importlib.import_module("banbot.rooms")

    monkeypatch.setattr(invite_module, "ADMIN_ROOM", "admin@conference.example.org")
    monkeypatch.setattr(rooms_module, "ADMIN_ROOM", "admin@conference.example.org")
    monkeypatch.setattr(rooms_module, "NICK", "BanBot")

    bot = RoomHealthBot()
    bot.protected_rooms = set()
    bot.occupants["stored@conference.example.test"] = {"BanBot": {"jid": "bot@example.org"}}
    await bot.setup_db()
    try:
        invite = await bot._store_pending_room_invite(
            "stored@conference.example.test",
            "alice@example.org",
            "please add",
        )

        await bot.cmd_room_invite(["accept", str(invite["id"])], "admin@conference.example.org")

        assert "stored@conference.example.test" in bot.protected_rooms
        assert bot.pending_room_invites == {}
        async with bot.db.execute("SELECT COUNT(*) FROM room_invites") as cursor:
            row = await cursor.fetchone()
        assert row[0] == 0
    finally:
        await bot.db.close()


@pytest.mark.asyncio
async def test_room_invite_decline_removes_persisted_invite(temp_db_path, monkeypatch):
    invite_module = importlib.import_module("banbot.rooms.invites")

    monkeypatch.setattr(invite_module, "ADMIN_ROOM", "admin@conference.example.org")

    bot = RoomHealthBot()
    await bot.setup_db()
    try:
        invite = await bot._store_pending_room_invite(
            "stored@conference.example.test",
            "alice@example.org",
            "please add",
        )

        await bot.cmd_room_invite(["decline", str(invite["id"])], "admin@conference.example.org")

        assert bot.pending_room_invites == {}
        async with bot.db.execute("SELECT COUNT(*) FROM room_invites") as cursor:
            row = await cursor.fetchone()
        assert row[0] == 0
    finally:
        await bot.db.close()


@pytest.mark.asyncio
async def test_health_check_alerts_can_be_disabled(monkeypatch):
    bot = RoomHealthBot()
    bot.occupants = {"room@conference.example.test": {}}
    bot.alert_on_health_check_failure = False
    bot.get_db_stats = lambda: {"db_size_bytes": 0}

    calls = {"count": 0}

    async def fake_sleep(delay):
        calls["count"] += 1
        if calls["count"] > 1:
            raise asyncio.CancelledError()

    async def fake_stats():
        return {"db_size_bytes": 0}

    bot.get_db_stats = fake_stats
    monkeypatch.setattr("banbot.health_check.asyncio.sleep", fake_sleep)

    with pytest.raises(asyncio.CancelledError):
        await bot.health_check_worker()

    assert bot.sent == []


@pytest.mark.asyncio
async def test_health_check_reports_database_stats_failure(monkeypatch):
    bot = RoomHealthBot()
    bot.protected_rooms = set()

    async def broken_stats():
        raise RuntimeError("database locked")

    bot.get_db_stats = broken_stats

    calls = {"count": 0}

    async def fake_sleep(delay):
        calls["count"] += 1
        if calls["count"] > 1:
            raise asyncio.CancelledError()

    monkeypatch.setattr("banbot.health_check.asyncio.sleep", fake_sleep)

    with pytest.raises(asyncio.CancelledError):
        await bot.health_check_worker()

    assert any("Database stats failed" in item["mbody"] for item in bot.sent)
    assert any("database locked" in item["mbody"] for item in bot.sent)


@pytest.mark.asyncio
async def test_health_check_reports_database_size_limit(monkeypatch):
    bot = RoomHealthBot()
    bot.protected_rooms = set()
    bot.alert_on_db_size_mb = 1

    async def large_stats():
        return {"db_size_bytes": 2 * 1024 * 1024}

    bot.get_db_stats = large_stats

    calls = {"count": 0}

    async def fake_sleep(delay):
        calls["count"] += 1
        if calls["count"] > 1:
            raise asyncio.CancelledError()

    monkeypatch.setattr("banbot.health_check.asyncio.sleep", fake_sleep)

    with pytest.raises(asyncio.CancelledError):
        await bot.health_check_worker()

    assert any("Database size alert" in item["mbody"] for item in bot.sent)
    assert any("2.0 MiB" in item["mbody"] for item in bot.sent)


@pytest.mark.asyncio
async def test_room_invite_accept_and_decline_validate_missing_or_bad_ids():
    bot = RoomHealthBot()

    await bot.cmd_room_invite(["accept"], "admin@conference.example.org")
    assert "Usage" in bot.sent[-1]["mbody"]

    await bot.cmd_room_invite(["accept", "not-a-number"], "admin@conference.example.org")
    assert "Invite id must be a number" in bot.sent[-1]["mbody"]

    await bot.cmd_room_invite(["decline", "123"], "admin@conference.example.org")
    assert "Unknown pending room invite id: 123" in bot.sent[-1]["mbody"]


@pytest.mark.asyncio
async def test_room_invite_unknown_action_shows_usage():
    bot = RoomHealthBot()

    await bot.cmd_room_invite(["wat"], "admin@conference.example.org")

    assert "Unknown room invite action: wat" in bot.sent[-1]["mbody"]
    assert "room invite list" in bot.sent[-1]["mbody"]


@pytest.mark.asyncio
async def test_room_invite_cleanup_expired_only_removes_old_invites(monkeypatch):
    invite_module = importlib.import_module("banbot.rooms.invites")

    monkeypatch.setattr(invite_module, "ADMIN_ROOM", "admin@conference.example.org")
    bot = RoomHealthBot()
    bot.room_invite_max_age_days = 30
    now = int(time.time())
    bot.pending_room_invites = {
        1: {"id": 1, "room_jid": "old@conference.example.test", "inviter": "alice@example.org", "reason": "", "created_at": now - 31 * 86400},
        2: {"id": 2, "room_jid": "new@conference.example.test", "inviter": "bob@example.org", "reason": "", "created_at": now},
    }
    bot.pending_room_invite_index = {
        (str(invite["room_jid"]), str(invite["inviter"])): idx
        for idx, invite in bot.pending_room_invites.items()
    }

    await bot.cmd_room_invite(["cleanup", "expired"], "admin@conference.example.org")

    assert sorted(bot.pending_room_invites) == [2]
    assert "Deleted pending invites: 1" in bot.sent[-1]["mbody"]


@pytest.mark.asyncio
async def test_room_invite_cleanup_expired_respects_disabled_expiry():
    bot = RoomHealthBot()
    bot.room_invite_max_age_days = 0
    bot.pending_room_invites = {
        1: {"id": 1, "room_jid": "old@conference.example.test", "inviter": "alice@example.org", "reason": "", "created_at": int(time.time()) - 365 * 86400},
    }
    bot.pending_room_invite_index = {("old@conference.example.test", "alice@example.org"): 1}

    await bot.cmd_room_invite(["cleanup", "expired"], "admin@conference.example.org")

    assert sorted(bot.pending_room_invites) == [1]
    assert "Invite expiry is disabled" in bot.sent[-1]["mbody"]


@pytest.mark.asyncio
async def test_room_invite_accept_keeps_invite_when_room_add_fails():
    bot = RoomHealthBot()
    bot.pending_room_invites = {
        1: {"id": 1, "room_jid": "broken@conference.example.test", "inviter": "alice@example.org", "reason": "", "created_at": int(time.time())},
    }
    bot.pending_room_invite_index = {("broken@conference.example.test", "alice@example.org"): 1}

    async def fake_cmd_room(args, room):
        await bot.bot_send_message(mto=room, mbody="❌ Room add failed", mtype="groupchat")

    bot.cmd_room = fake_cmd_room

    await bot.cmd_room_invite(["accept", "1"], "admin@conference.example.org")

    assert sorted(bot.pending_room_invites) == [1]
    assert "invite remains pending" in bot.sent[-1]["mbody"]


@pytest.mark.asyncio
async def test_room_invite_rm_alias_declines_pending_invite():
    bot = RoomHealthBot()
    bot.pending_room_invites = {
        1: {"id": 1, "room_jid": "new@conference.example.test", "inviter": "alice@example.org", "reason": "", "created_at": int(time.time())},
    }
    bot.pending_room_invite_index = {("new@conference.example.test", "alice@example.org"): 1}

    await bot.cmd_room_invite(["rm", "1"], "admin@conference.example.org")

    assert bot.pending_room_invites == {}
    assert "Declined protected-room invite #1" in bot.sent[-1]["mbody"]


@pytest.mark.asyncio
async def test_room_list_shows_join_state_and_bot_affiliation():
    bot = RoomHealthBot()
    room = "room@conference.example.test"
    bot.occupants = {
        room: {
            "BanBot": {
                "jid": "bot@example.org",
                "affiliation": "owner",
            }
        }
    }

    await bot.cmd_room(["list", "all"], "admin@conference.example.org")
    body = bot.sent[-1]["mbody"]

    assert f"🟢 {room} | joined | bot affiliation: owner" in body

    bot.occupants[room] = {}
    await bot.cmd_room(["list", "all"], "admin@conference.example.org")
    assert f"🔴 {room} | not joined | bot affiliation: unknown" in bot.sent[-1]["mbody"]


@pytest.mark.asyncio
async def test_startup_version_notice_is_persisted_and_announced_after_upgrade(
    temp_db_path, monkeypatch
):
    updates_module = importlib.import_module("banbot.updates")
    bot = RoomHealthBot()
    bot.announce_startup = True
    await bot.setup_db()
    try:
        # First successful start initializes the baseline without claiming an update.
        await bot.prepare_startup_version_notice(reconnecting=False)
        assert await bot.finalize_startup_version_notice(reconnecting=False) is False
        assert bot.sent == []

        await bot.db.execute(
            "UPDATE bot_metadata SET value = ? WHERE key = ?",
            ("2.5.9", "last_successful_start_version"),
        )
        await bot.db.commit()
        monkeypatch.setattr(updates_module, "__version__", "2.6.1")

        assert await bot.prepare_startup_version_notice(reconnecting=False) == "2.5.9"
        assert await bot.finalize_startup_version_notice(reconnecting=False) is True
        assert "BanBot updated successfully: 2.5.9 → 2.6.1" in bot.sent[-1]["mbody"]

        async with bot.db.execute(
            "SELECT value FROM bot_metadata WHERE key = ?",
            ("last_successful_start_version",),
        ) as cursor:
            row = await cursor.fetchone()
        assert row == ("2.6.1",)
    finally:
        await bot.db.close()


@pytest.mark.asyncio
async def test_startup_version_notice_skips_reconnects(temp_db_path):
    bot = RoomHealthBot()
    await bot.setup_db()
    try:
        assert await bot.prepare_startup_version_notice(reconnecting=True) is None
        assert await bot.finalize_startup_version_notice(reconnecting=True) is False
        assert bot.sent == []
    finally:
        await bot.db.close()

@pytest.mark.asyncio
async def test_room_rejoin_retries_one_room_and_syncs_bans():
    bot = RoomHealthBot()
    target = "room@conference.example.test"
    bot.bot_admin_state = {}
    bot.room_bot_nicks = {target: "BanBot"}
    bot.occupants = {
        target: {
            "BanBot": {
                "jid": "bot@example.org/resource",
                "affiliation": "admin",
                "role": "moderator",
            }
        }
    }
    bot.muc_join_timeout_seconds = 45
    bot.muc_join_retries = 3
    calls = []

    async def ensure_joined(room, *, force=False, **kwargs):
        calls.append((room, force, kwargs))
        return True

    bot.ensure_muc_joined = ensure_joined

    await bot.cmd_room(["rejoin", target], "admin@conference.example.org")

    assert calls == [(target, True, {})]
    assert bot.bot_admin_state[target] is True
    assert bot.synced_room == target
    assert "3 attempt(s) and a 45s timeout" in bot.sent[-2]["mbody"]
    assert f"🟢 {target} | joined | bot affiliation: admin" in bot.sent[-1]["mbody"]


@pytest.mark.asyncio
async def test_room_rejoin_reports_failed_and_unprotected_rooms():
    bot = RoomHealthBot()
    target = "room@conference.example.test"
    bot.bot_admin_state = {target: True}

    async def ensure_joined(room, *, force=False, **kwargs):
        assert room == target
        assert force is True
        return False

    bot.ensure_muc_joined = ensure_joined

    await bot.cmd_room(["rejoin", target], "admin@conference.example.org")
    assert target not in bot.bot_admin_state
    assert f"🔴 {target} | join failed" in bot.sent[-1]["mbody"]

    await bot.cmd_room(["rejoin", "other@conference.example.test"], "admin@conference.example.org")
    assert bot.sent[-1]["mbody"] == "❌ Room is not protected: other@conference.example.test"
