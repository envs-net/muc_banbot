import asyncio
import importlib

import pytest

pytest.importorskip("slixmpp")

from banbot.admin import AdminMixin
from banbot.muc import MucMixin
from banbot.occupants import BotOccupantMixin
from banbot.utils import bare_jid


class FakeMucPlugin:
    def __init__(self, owners=None, admins=None):
        self.owners = owners or []
        self.admins = admins or []
        self.calls = []

    async def get_users_by_affiliation(self, room, affiliation):
        self.calls.append((room, affiliation))
        if affiliation == "owner":
            return self.owners
        if affiliation == "admin":
            return self.admins
        return []


class AdminBot(AdminMixin):
    def __init__(self):
        self.occupants = {
            "admin@conference.example.test": {
                "Root": {"jid": "root@example.test/device", "affiliation": "owner"},
                "User": {"jid": "user@example.test/device", "affiliation": "member"},
            },
            "room@conference.example.test": {
                "AdminNick": {"jid": "admin@example.test/laptop", "affiliation": "admin"},
                "Regular": {"jid": "regular@example.test/phone", "affiliation": "member"},
            },
        }
        self.protected_rooms = {"room@conference.example.test"}
        self.admin_affiliation_query_forbidden_rooms = set()
        self.plugin = {"xep_0045": FakeMucPlugin()}
        self.boundjid = type("BoundJid", (), {"bare": "bot@example.test"})()
        self.bot_admin_state = {}
        self.sent = []

    def bare_jid(self, jid):
        return bare_jid(jid)

    async def bot_send_message(self, **kwargs):
        self.sent.append(kwargs)


def test_admin_and_muc_mixins_share_one_bot_occupant_lookup():
    assert "_bot_occupant_entry" not in AdminMixin.__dict__
    assert "_bot_occupant_entry" not in MucMixin.__dict__
    assert AdminMixin._bot_occupant_entry is BotOccupantMixin._bot_occupant_entry
    assert MucMixin._bot_occupant_entry is BotOccupantMixin._bot_occupant_entry


def test_is_admin_or_owner_uses_live_occupant_cache():
    bot = AdminBot()

    assert bot.is_admin_or_owner("room@conference.example.test", nick="AdminNick")
    assert bot.is_admin_or_owner("room@conference.example.test", jid="admin@example.test/resource")
    assert not bot.is_admin_or_owner("room@conference.example.test", nick="Regular")


def test_is_bot_admin_or_owner_falls_back_to_bound_jid_when_nick_changes():
    bot = AdminBot()
    bot.occupants["room@conference.example.test"] = {
        "BanBot-alt": {
            "jid": "bot@example.test/new-resource",
            "affiliation": "admin",
        }
    }

    assert bot.is_bot_admin_or_owner("room@conference.example.test") is True


def test_is_authorized_requires_admin_room_and_admin_affiliation(monkeypatch, fake_msg_factory):
    admin_module = importlib.import_module("banbot.admin")

    monkeypatch.setattr(admin_module, "ADMIN_ROOM", "admin@conference.example.test")
    bot = AdminBot()

    admin_msg = fake_msg_factory(room="admin@conference.example.test", nick="Root", body="!status")
    user_msg = fake_msg_factory(room="admin@conference.example.test", nick="User", body="!status")
    other_room_msg = fake_msg_factory(room="room@conference.example.test", nick="AdminNick", body="!status")

    assert bot.is_authorized(admin_msg)
    assert not bot.is_authorized(user_msg)
    assert not bot.is_authorized(other_room_msg)


def test_is_authorized_normalizes_room_case_and_rejects_blank_nick(monkeypatch, fake_msg_factory):
    admin_module = importlib.import_module("banbot.admin")

    monkeypatch.setattr(admin_module, "ADMIN_ROOM", "Admin@Conference.Example.Test")
    bot = AdminBot()

    admin_msg = fake_msg_factory(room="admin@conference.example.test", nick="Root", body="!status")
    assert bot.is_authorized(admin_msg)

    admin_msg["mucnick"] = "   "
    assert not bot.is_authorized(admin_msg)


@pytest.mark.asyncio
async def test_protected_admin_target_detects_cached_admin_nick(monkeypatch):
    admin_module = importlib.import_module("banbot.admin")

    monkeypatch.setattr(admin_module, "ADMIN_ROOM", "admin@conference.example.test")
    bot = AdminBot()

    protected, reason = await bot.is_protected_admin_target("AdminNick", nick="AdminNick")

    assert protected is True
    assert "admin/owner" in reason


@pytest.mark.asyncio
async def test_protected_admin_target_detects_server_affiliation_jid(monkeypatch):
    admin_module = importlib.import_module("banbot.admin")

    monkeypatch.setattr(admin_module, "ADMIN_ROOM", "admin@conference.example.test")
    bot = AdminBot()
    bot.plugin["xep_0045"] = FakeMucPlugin(owners=["owner@example.test"], admins=[])

    protected, reason = await bot.is_protected_admin_target("owner@example.test")

    assert protected is True
    assert "owner@example.test" in reason


@pytest.mark.asyncio
async def test_protected_admin_target_detects_domain_ban_covering_admin(monkeypatch):
    admin_module = importlib.import_module("banbot.admin")

    monkeypatch.setattr(admin_module, "ADMIN_ROOM", "admin@conference.example.test")
    bot = AdminBot()
    bot.plugin["xep_0045"] = FakeMucPlugin(owners=["owner@example.test"], admins=[])

    protected, reason = await bot.is_protected_admin_target("*.example.test")

    assert protected is True
    assert "domain ban" in reason


@pytest.mark.asyncio
async def test_forbidden_affiliation_query_logs_expected_admin_fallback(monkeypatch, caplog):
    admin_module = importlib.import_module("banbot.admin")

    class FakeIqError(Exception):
        condition = "forbidden"
        text = ""

    class ForbiddenMucPlugin:
        def __init__(self):
            self.calls = 0

        async def get_users_by_affiliation(self, room, affiliation):
            self.calls += 1
            raise FakeIqError("forbidden")

    bot = AdminBot()
    plugin = ForbiddenMucPlugin()
    bot.plugin["xep_0045"] = plugin
    room = "room@conference.example.test"

    with caplog.at_level("WARNING", logger="banbot.admin"):
        assert await bot.get_room_admin_owner_jids(room) == set()
        assert await bot.get_room_admin_owner_jids(room) == set()

    assert plugin.calls == 1
    assert "expected when BanBot is room admin rather than owner" in caplog.text
    assert "offline admins cannot be detected" in caplog.text
    assert "Server forbids" not in caplog.text


@pytest.mark.asyncio
async def test_successful_affiliation_queries_are_reused_briefly(monkeypatch):
    admin_module = importlib.import_module("banbot.admin")

    monkeypatch.setattr(admin_module, "ADMIN_ROOM", "admin@conference.example.test")
    bot = AdminBot()
    plugin = FakeMucPlugin(owners=["owner@example.test"], admins=["admin@example.test"])
    bot.plugin["xep_0045"] = plugin
    room = "room@conference.example.test"

    first = await bot.get_room_admin_owner_jids(room)
    second = await bot.get_room_admin_owner_jids(room)

    assert first == {"owner@example.test", "admin@example.test"}
    assert second == first
    assert plugin.calls == [(room, "owner"), (room, "admin")]


@pytest.mark.asyncio
async def test_admin_protection_checks_managed_rooms_concurrently(monkeypatch):
    admin_module = importlib.import_module("banbot.admin")
    admin_room = "admin@conference.example.test"
    protected_room = "room@conference.example.test"
    monkeypatch.setattr(admin_module, "ADMIN_ROOM", admin_room)

    class BarrierMucPlugin:
        def __init__(self):
            self.owner_rooms: set[str] = set()
            self.owner_queries_started = asyncio.Event()

        async def get_users_by_affiliation(self, room, affiliation):
            if affiliation == "owner":
                self.owner_rooms.add(room)
                if self.owner_rooms == {admin_room, protected_room}:
                    self.owner_queries_started.set()
                await self.owner_queries_started.wait()
                if room == protected_room:
                    return ["offline-owner@example.test"]
            return []

    bot = AdminBot()
    bot.plugin["xep_0045"] = BarrierMucPlugin()

    protected, reason = await asyncio.wait_for(
        bot.is_protected_admin_target("offline-owner@example.test"),
        timeout=0.5,
    )

    assert protected is True
    assert reason == f"offline-owner@example.test is admin/owner in {protected_room}"

@pytest.mark.asyncio
async def test_explicit_affiliation_refresh_reprobes_room_after_forbidden():
    class FakeIqError(Exception):
        condition = "forbidden"
        text = ""

    class RecoveringMucPlugin:
        def __init__(self):
            self.forbidden = True
            self.calls: list[tuple[str, str]] = []

        async def get_users_by_affiliation(self, room, affiliation):
            self.calls.append((room, affiliation))
            if self.forbidden:
                raise FakeIqError("forbidden")
            if affiliation == "owner":
                return ["owner@example.test"]
            if affiliation == "admin":
                return ["admin@example.test"]
            return []

    bot = AdminBot()
    plugin = RecoveringMucPlugin()
    bot.plugin["xep_0045"] = plugin
    room = "room@conference.example.test"

    assert await bot.get_room_admin_owner_jids(room) == set()
    assert room in bot.admin_affiliation_query_forbidden_rooms
    first_call_count = len(plugin.calls)

    plugin.forbidden = False
    assert await bot.get_room_admin_owner_jids(room) == set()
    assert len(plugin.calls) == first_call_count

    refreshed = await bot.get_room_admin_owner_jids(room, refresh=True)

    assert refreshed == {"owner@example.test", "admin@example.test"}
    assert room not in bot.admin_affiliation_query_forbidden_rooms
    assert plugin.calls[first_call_count:] == [
        (room, "owner"),
        (room, "admin"),
    ]


@pytest.mark.asyncio
async def test_check_bot_admin_rights_reports_success_when_joined_with_rights(monkeypatch):
    admin_module = importlib.import_module("banbot.admin")
    room = "room@conference.example.test"
    monkeypatch.setattr(admin_module, "ADMIN_ROOM", "admin@conference.example.test")
    bot = AdminBot()
    bot.occupants[room]["BanBot"] = {
        "jid": "bot@example.test/resource",
        "affiliation": "admin",
        "role": "moderator",
    }

    await bot.check_bot_admin_rights()

    assert bot.bot_admin_state == {room: True}
    assert bot.sent == [
        {
            "mto": "admin@conference.example.test",
            "mbody": "✅ Bot has admin/owner rights in all protected rooms.",
            "mtype": "groupchat",
        }
    ]


@pytest.mark.asyncio
async def test_check_bot_admin_rights_reports_not_joined_and_missing_rights(monkeypatch):
    admin_module = importlib.import_module("banbot.admin")
    admin_room = "admin@conference.example.test"
    joined_room = "joined@conference.example.test"
    missing_room = "missing@conference.example.test"
    monkeypatch.setattr(admin_module, "ADMIN_ROOM", admin_room)

    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr(admin_module.asyncio, "sleep", no_sleep)
    bot = AdminBot()
    bot.protected_rooms = {joined_room, missing_room}
    bot.occupants[joined_room] = {
        "BanBot": {
            "jid": "bot@example.test/resource",
            "affiliation": "member",
            "role": "participant",
        }
    }
    bot.bot_admin_state[missing_room] = True

    await bot.check_bot_admin_rights()

    assert bot.bot_admin_state == {joined_room: False}
    assert len(bot.sent) == 1
    message = bot.sent[0]["mbody"]
    assert "Not joined:\nmissing@conference.example.test" in message
    assert "Joined without admin/owner rights:\njoined@conference.example.test" in message


@pytest.mark.asyncio
async def test_whoami_reports_admin_permissions_and_jid_in_admin_room(monkeypatch):
    admin_module = importlib.import_module("banbot.admin")
    admin_room = "admin@conference.example.test"
    monkeypatch.setattr(admin_module, "ADMIN_ROOM", admin_room)
    bot = AdminBot()

    await bot._cmd_whoami(admin_room, "Root")

    message = bot.sent[-1]["mbody"]
    assert "JID: root@example.test" in message
    assert "Affiliation: owner" in message
    assert "✅ Can ban/kick users" in message
    assert "✅ Can manage room" in message


@pytest.mark.asyncio
async def test_whoami_reports_regular_participant_without_exposing_jid_outside_admin_room(monkeypatch):
    admin_module = importlib.import_module("banbot.admin")
    monkeypatch.setattr(admin_module, "ADMIN_ROOM", "admin@conference.example.test")
    bot = AdminBot()

    await bot._cmd_whoami("room@conference.example.test", "Regular")

    message = bot.sent[-1]["mbody"]
    assert "❌ Regular participant" in message
    assert "Affiliation: member" in message
    assert "JID:" not in message
