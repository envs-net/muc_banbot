import pytest

pytest.importorskip("slixmpp")

from banbot.admin import AdminMixin
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


def test_is_admin_or_owner_uses_live_occupant_cache(monkeypatch):
    import banbot.admin as admin_module

    monkeypatch.setattr(admin_module, "NICK", "BanBot")
    bot = AdminBot()

    assert bot.is_admin_or_owner("room@conference.example.test", nick="AdminNick")
    assert bot.is_admin_or_owner("room@conference.example.test", jid="admin@example.test/resource")
    assert not bot.is_admin_or_owner("room@conference.example.test", nick="Regular")


def test_is_authorized_requires_admin_room_and_admin_affiliation(monkeypatch, fake_msg_factory):
    import banbot.admin as admin_module

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
    import banbot.admin as admin_module

    monkeypatch.setattr(admin_module, "ADMIN_ROOM", "admin@conference.example.test")
    bot = AdminBot()

    protected, reason = await bot.is_protected_admin_target("AdminNick", nick="AdminNick")

    assert protected is True
    assert "admin/owner" in reason


@pytest.mark.asyncio
async def test_protected_admin_target_detects_server_affiliation_jid(monkeypatch):
    import banbot.admin as admin_module

    monkeypatch.setattr(admin_module, "ADMIN_ROOM", "admin@conference.example.test")
    bot = AdminBot()
    bot.plugin["xep_0045"] = FakeMucPlugin(owners=["owner@example.test"], admins=[])

    protected, reason = await bot.is_protected_admin_target("owner@example.test")

    assert protected is True
    assert "owner@example.test" in reason


@pytest.mark.asyncio
async def test_protected_admin_target_detects_domain_ban_covering_admin(monkeypatch):
    import banbot.admin as admin_module

    monkeypatch.setattr(admin_module, "ADMIN_ROOM", "admin@conference.example.test")
    bot = AdminBot()
    bot.plugin["xep_0045"] = FakeMucPlugin(owners=["owner@example.test"], admins=[])

    protected, reason = await bot.is_protected_admin_target("*.example.test")

    assert protected is True
    assert "domain ban" in reason
