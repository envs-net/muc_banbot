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
        pass

    class ForbiddenMucPlugin:
        def __init__(self):
            self.calls = 0

        async def get_users_by_affiliation(self, room, affiliation):
            self.calls += 1
            raise FakeIqError("forbidden")

    monkeypatch.setattr(admin_module, "IqError", FakeIqError)
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
