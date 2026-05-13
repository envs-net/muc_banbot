"""Additional room, health-check and update helper tests."""

from __future__ import annotations

import asyncio
import time

import pytest

aiosqlite = pytest.importorskip("aiosqlite")

from banbot.db import DatabaseMixin
from banbot.health_check import HealthCheckMixin
from banbot.rooms import RoomMixin
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


class RoomHealthBot(DatabaseMixin, RoomMixin, HealthCheckMixin, UpdateMixin):
    def __init__(self):
        self.protected_rooms = {"room@conference.example.test"}
        self.registered_rooms = set()
        self.event_handlers = []
        self.occupants = {"room@conference.example.test": {"BanBot": {"jid": "bot@example.org"}}}
        self.plugin = {
            "xep_0030": FakeDisco([("conference", "text", "Room")]),
            "xep_0045": FakeMucPlugin(),
        }
        self.sent = []
        self.command_prefix = "!"
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
    import banbot.rooms as rooms_module

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

        await bot.cmd_room(["remove", "new@conference.example.test"], "admin@conference.example.org")
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
