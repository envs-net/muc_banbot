"""MUC presence, reconnect and occupant-flow tests with fake stanzas."""

from __future__ import annotations

import asyncio
import time

import pytest

aiosqlite = pytest.importorskip("aiosqlite")

from banbot.cache import CacheMixin
from banbot.db import DatabaseMixin
from banbot.muc import MucMixin
from banbot.utils import bare_jid


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
    ) -> None:
        self._from = type("From", (), {"bare": room, "resource": nick})()
        self._muc = FakeMuc(nick=nick, jid=jid, affiliation=affiliation, role=role)

    def __getitem__(self, key):
        if key == "from":
            return self._from
        if key == "muc":
            return self._muc
        raise KeyError(key)


class MucTestBot(MucMixin, DatabaseMixin, CacheMixin):
    def __init__(self):
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
        self.verify_result = False
        self.connected = False
        self.show_ban_in_muc = True

    @staticmethod
    def bare_jid(jid):
        return bare_jid(jid)

    def connect(self):
        self.connected = True

    def is_admin_or_owner(self, room, nick=None, jid=None):
        info = self.occupants.get(room, {}).get(nick or "")
        return bool(info and info.get("affiliation") in ("admin", "owner"))

    async def check_jid_against_rtbl(self, jid, nick):
        self.rtbl_checks.append((jid, nick))
        return False

    async def apply_ban_to_room(self, room, ban_jid, ban_nick, comment, issuer=None):
        self.applied.append((room, ban_jid, ban_nick, comment, issuer))

    async def verify_admin_rights(self, room):
        return self.verify_result

    async def bot_send_message(self, **kwargs):
        self.sent.append(kwargs)


@pytest.mark.asyncio
async def test_muc_online_updates_occupants_and_applies_matching_jid_and_domain_bans(temp_db_path):
    bot = MucTestBot()
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
    bot = MucTestBot()
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
async def test_on_muc_presence_warns_when_bot_loses_admin(monkeypatch):
    import banbot.muc as muc_module

    monkeypatch.setattr(muc_module, "ADMIN_ROOM", "admin@conference.example.test")
    monkeypatch.setattr(muc_module, "NICK", "BanBot")
    bot = MucTestBot()
    bot.bot_admin_state["room@conference.example.test"] = True
    bot.room_join_time["room@conference.example.test"] = time.time() - 10
    bot.verify_result = False

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
    await bot.reconnect_task
