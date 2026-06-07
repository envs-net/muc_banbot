"""MUC presence, reconnect and occupant-flow tests with fake stanzas."""

from __future__ import annotations

import asyncio
import time
from xml.etree import ElementTree as ET

import pytest

aiosqlite = pytest.importorskip("aiosqlite")

from banbot.cache import CacheMixin
from banbot.db import DatabaseMixin
from banbot.muc import MucMixin
from banbot.utils import bare_jid


MUC_USER_NS = "http://jabber.org/protocol/muc#user"
MUC_USER_TAG = f"{{{MUC_USER_NS}}}"


class FakeMuc(dict):
    pass


class FakePresence:
    def __init__(
        self,
        *,
        room: str = "room@conference.example.test",
        nick: str = "User",
        jid: str | None = "user@example.test/resource",
        affiliation: str = "member",
        role: str = "participant",
        status_codes: set[str] | None = None,
        reason: str | None = None,
    ) -> None:
        self._from = type("From", (), {"bare": room, "resource": nick})()
        self._muc = FakeMuc(nick=nick, jid=jid, affiliation=affiliation, role=role)
        if status_codes is not None:
            self._muc["status_codes"] = status_codes
        if reason is not None:
            self._muc["reason"] = reason

        self.xml = ET.Element("presence")
        x = ET.SubElement(self.xml, f"{MUC_USER_TAG}x")
        item = ET.SubElement(
            x,
            f"{MUC_USER_TAG}item",
            {"affiliation": affiliation, "role": role},
        )
        if reason is not None:
            reason_el = ET.SubElement(item, f"{MUC_USER_TAG}reason")
            reason_el.text = reason
        for code in status_codes or set():
            ET.SubElement(x, f"{MUC_USER_TAG}status", {"code": code})

    def __getitem__(self, key):
        if key == "from":
            return self._from
        if key == "muc":
            return self._muc
        raise KeyError(key)


class MucTestBot(MucMixin, DatabaseMixin, CacheMixin):
    def __init__(self, db_path: str | None = None):
        self.db_path = db_path
        self.protected_rooms = {"room@conference.example.test"}
        self.occupants = {}
        self.bot_admin_state = {}
        self.room_join_time = {}
        self.reconnecting = False
        self.reconnect_task = None
        self.ban_cache = {}
        self.ban_index_by_jid = {}
        self.ban_index_by_nick = {}
        self.ban_index_by_domain = {}
        self.sent = []
        self.applied = []
        self.rtbl_checks = []
        self.manual_redactions = []
        self.unbans = []
        self.verify_result = False
        self.connected = False
        self.show_ban_in_muc = True

    @staticmethod
    def bare_jid(jid: str) -> str:
        return bare_jid(jid)

    def connect(self) -> None:
        self.connected = True

    def is_admin_or_owner(
        self,
        room: str,
        nick: str | None = None,
        jid: str | None = None,
    ) -> bool:
        info = self.occupants.get(room, {}).get(nick or "")
        return bool(info and info.get("affiliation") in ("admin", "owner"))

    async def check_jid_against_rtbl(self, jid: str, nick: str) -> bool:
        self.rtbl_checks.append((jid, nick))
        return False

    async def apply_ban_to_room(
        self,
        room: str,
        ban_jid: str | None,
        ban_nick: str | None,
        comment: str | None,
        issuer: str | None = None,
    ) -> None:
        self.applied.append((room, ban_jid, ban_nick, comment, issuer))

    async def verify_admin_rights(self, room):
        return self.verify_result

    async def bot_send_message(self, **kwargs):
        self.sent.append(kwargs)

    async def maybe_auto_redact_after_manual_muc_ban(self, jid, comment, actor=None):
        self.manual_redactions.append((jid, comment, actor))

    async def unban_all(self, jid, issuer="system"):
        self.unbans.append((jid, issuer))


@pytest.mark.asyncio
async def test_muc_online_updates_occupants_and_applies_matching_jid_and_domain_bans(temp_db_path):
    bot = MucTestBot(temp_db_path)
    await bot.setup_db()
    try:
        await bot.upsert_ban_db("user@example.test", "User", 0, "tester", "jid ban")
        await bot.upsert_ban_db("*.example.test", None, 0, "tester", "domain ban")
        await bot.load_bans_from_db()

        await bot.muc_online(
            FakePresence(
                room="room@conference.example.test",
                nick="User",
                jid="user@example.test/resource",
            )
        )

        assert bot.occupants["room@conference.example.test"]["User"]["jid"] == "user@example.test/resource"
        assert bot.rtbl_checks == [("user@example.test/resource", "User")]
        assert ("room@conference.example.test", "user@example.test", "user", "jid ban", None) in bot.applied
        assert ("room@conference.example.test", "*.example.test", None, "domain ban", None) in bot.applied
    finally:
        await bot.db.close()


@pytest.mark.asyncio
async def test_muc_online_converts_nick_only_ban_to_jid_ban(temp_db_path):
    bot = MucTestBot(temp_db_path)
    await bot.setup_db()
    try:
        await bot.upsert_ban_db(None, "BadNick", 0, "tester", "nick ban")
        await bot.load_bans_from_db()

        await bot.muc_online(
            FakePresence(
                room="room@conference.example.test",
                nick="BadNick",
                jid="bad@example.test/resource",
            )
        )

        await bot.load_bans_from_db()
        assert "bad@example.test" in bot.ban_index_by_jid
        assert "badnick" not in bot.ban_index_by_nick
        assert any(call[1] == "bad@example.test" for call in bot.applied)
    finally:
        await bot.db.close()


@pytest.mark.asyncio
async def test_muc_offline_removes_occupant():
    bot = MucTestBot()
    bot.occupants = {
        "room@conference.example.test": {
            "User": {"jid": "user@example.test/resource", "affiliation": "member", "role": "participant"}
        }
    }

    await bot.muc_offline(FakePresence(room="room@conference.example.test", nick="User"))

    assert "User" not in bot.occupants["room@conference.example.test"]


@pytest.mark.asyncio
async def test_muc_offline_recovers_manual_ban_and_auto_redacts(temp_db_path):
    bot = MucTestBot(temp_db_path)
    await bot.setup_db()
    bot.occupants = {
        "room@conference.example.test": {
            "User": {"jid": "user@example.test/resource", "affiliation": "member", "role": "participant"}
        }
    }

    try:
        await bot.muc_offline(
            FakePresence(
                room="room@conference.example.test",
                nick="User",
                status_codes={"301"},
                reason="spam",
            )
        )

        await bot.load_bans_from_db()
        assert "user@example.test" in bot.ban_index_by_jid
        assert bot.ban_index_by_jid["user@example.test"][4] == "spam"
        assert bot.manual_redactions == [("user@example.test", "spam", "manual_muc_ban")]
    finally:
        await bot.db.close()


@pytest.mark.asyncio
async def test_muc_offline_recovers_manual_ban_reason_from_xml(temp_db_path):
    bot = MucTestBot(temp_db_path)
    await bot.setup_db()
    bot.occupants = {
        "room@conference.example.test": {
            "User": {"jid": "user@example.test/resource", "affiliation": "member", "role": "participant"}
        }
    }

    try:
        presence = FakePresence(
            room="room@conference.example.test",
            nick="User",
            status_codes={"301"},
            reason=None,
        )
        reason_el = presence.xml.find(f".//{MUC_USER_TAG}reason")
        if reason_el is None:
            item = presence.xml.find(f".//{MUC_USER_TAG}item")
            reason_el = ET.SubElement(item, f"{MUC_USER_TAG}reason")
        reason_el.text = "spam"

        await bot.muc_offline(presence)

        await bot.load_bans_from_db()
        assert bot.ban_index_by_jid["user@example.test"][4] == "spam"
        assert bot.manual_redactions == [("user@example.test", "spam", "manual_muc_ban")]
    finally:
        await bot.db.close()


@pytest.mark.asyncio
async def test_on_muc_presence_warns_when_bot_loses_admin(monkeypatch):
    import banbot.muc as muc_module

    monkeypatch.setattr(muc_module, "ADMIN_ROOM", "admin@conference.example.test")
    monkeypatch.setattr(muc_module, "NICK", "BanBot")

    bot = MucTestBot()
    bot.bot_admin_state["room@conference.example.test"] = True
    bot.room_join_time["room@conference.example.test"] = time.time() - 10
    bot.verify_result = False
    bot.occupants["room@conference.example.test"] = {
        "BanBot": {
            "jid": "bot@example.test/service",
            "affiliation": "admin",
            "role": "moderator",
        }
    }

    await bot.on_muc_presence(
        FakePresence(
            room="room@conference.example.test",
            nick="BanBot",
            jid="bot@example.test/service",
            affiliation="member",
            role="participant",
        )
    )

    assert bot.bot_admin_state["room@conference.example.test"] is False
    assert "lost admin/owner rights" in bot.sent[-1]["mbody"]
    assert bot.occupants["room@conference.example.test"]["BanBot"] == {
        "jid": "bot@example.test/service",
        "affiliation": "member",
        "role": "participant",
    }


@pytest.mark.asyncio
async def test_on_muc_presence_warns_when_admin_room_state_only_exists_in_occupant_cache(monkeypatch):
    import banbot.muc as muc_module

    monkeypatch.setattr(muc_module, "ADMIN_ROOM", "admin@conference.example.test")
    monkeypatch.setattr(muc_module, "NICK", "BanBot")

    bot = MucTestBot()
    bot.room_join_time["admin@conference.example.test"] = time.time() - 10
    bot.verify_result = False

    # Simulate status-visible admin rights from the occupant cache, but no
    # initialized bot_admin_state yet.
    bot.occupants["admin@conference.example.test"] = {
        "BanBot": {
            "jid": "bot@example.test/service",
            "affiliation": "admin",
            "role": "moderator",
        }
    }

    await bot.on_muc_presence(
        FakePresence(
            room="admin@conference.example.test",
            nick="BanBot",
            jid="bot@example.test/service",
            affiliation="member",
            role="participant",
        )
    )

    assert bot.bot_admin_state["admin@conference.example.test"] is False
    assert bot.sent[-1]["mto"] == "admin@conference.example.test"
    assert "lost admin/owner rights in admin room admin@conference.example.test" in bot.sent[-1]["mbody"]
    assert bot.occupants["admin@conference.example.test"]["BanBot"] == {
        "jid": "bot@example.test/service",
        "affiliation": "member",
        "role": "participant",
    }


@pytest.mark.asyncio
async def test_on_disconnect_clears_runtime_state_and_schedules_reconnect(monkeypatch):
    bot = MucTestBot()
    bot.occupants = {"room": {"User": {}}}
    bot.bot_admin_state = {"room": True}
    bot.room_join_time = {"room": 123.0}

    async def fake_reconnect():
        return None

    monkeypatch.setattr(bot, "_delayed_reconnect", fake_reconnect)
    await bot.on_disconnect(None)

    assert bot.reconnecting is True
    assert bot.occupants == {}
    assert bot.bot_admin_state == {}
    assert bot.room_join_time == {}
    assert bot.reconnect_task is not None
    assert await bot.reconnect_task is None


@pytest.mark.asyncio
async def test_on_disconnect_does_not_schedule_overlapping_reconnects(monkeypatch):
    bot = MucTestBot()
    blocker = asyncio.Event()
    calls = {"count": 0}

    async def fake_reconnect():
        calls["count"] += 1
        await blocker.wait()

    monkeypatch.setattr(bot, "_delayed_reconnect", fake_reconnect)

    await bot.on_disconnect(None)
    first_task = bot.reconnect_task
    await bot.on_disconnect(None)

    assert bot.reconnect_task is first_task
    assert calls["count"] == 0

    blocker.set()
    assert await first_task is None


@pytest.mark.asyncio
async def test_delayed_reconnect_waits_for_session_start_signal(monkeypatch):
    import banbot.muc as muc_module

    bot = MucTestBot()
    bot.reconnecting = True
    calls = {"connect": 0}

    async def no_sleep(_delay):
        return None

    def fake_connect_with_config():
        calls["connect"] += 1
        bot._get_reconnect_success_event().set()
        return True

    monkeypatch.setattr(muc_module.asyncio, "sleep", no_sleep)
    bot.connect_with_config = fake_connect_with_config

    await bot._delayed_reconnect()

    assert calls["connect"] == 1


@pytest.mark.asyncio
async def test_delayed_reconnect_retries_until_session_start_signal(monkeypatch):
    import banbot.muc as muc_module

    bot = MucTestBot()
    bot.reconnecting = True
    calls = {"connect": 0}

    async def no_sleep(_delay):
        return None

    async def short_wait_for(awaitable, timeout):
        awaitable.close()
        if calls["connect"] < 2:
            raise asyncio.TimeoutError
        return None

    def fake_connect_with_config():
        calls["connect"] += 1
        if calls["connect"] >= 2:
            bot._get_reconnect_success_event().set()
        return True

    monkeypatch.setattr(muc_module.asyncio, "sleep", no_sleep)
    monkeypatch.setattr(muc_module.asyncio, "wait_for", short_wait_for)
    bot.connect_with_config = fake_connect_with_config

    await bot._delayed_reconnect()

    assert calls["connect"] == 2
