"""MUC presence, reconnect and occupant-flow tests with fake stanzas."""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from typing import TypedDict
from xml.etree import ElementTree as ET

import pytest

try:
    import aiosqlite
except ImportError:
    aiosqlite = None

pytestmark = pytest.mark.skipif(
    aiosqlite is None,
    reason="aiosqlite is required for MUC presence extra tests",
)

if aiosqlite is not None:
    from banbot.cache import CacheMixin
    from banbot.db import DatabaseMixin
    from banbot.muc import MucMixin
    from banbot.utils import bare_jid
else:  # pragma: no cover - tests are skipped when aiosqlite is unavailable.
    CacheMixin = DatabaseMixin = MucMixin = object
    bare_jid = None


MUC_USER_NS = "http://jabber.org/protocol/muc#user"
MUC_USER_TAG = f"{{{MUC_USER_NS}}}"

ROOM_JID = "room@conference.example.test"
ADMIN_ROOM_JID = "admin@conference.example.test"
USER_NICK = "User"
USER_NICK_NORMALIZED = USER_NICK.lower()
USER_BARE_JID = "user@example.test"
USER_JID_RESOURCE = f"{USER_BARE_JID}/resource"
BAD_NICK = "BadNick"
BAD_NICK_NORMALIZED = BAD_NICK.lower()
BAD_BARE_JID = "bad@example.test"
BAD_JID_RESOURCE = f"{BAD_BARE_JID}/resource"
BOT_NICK = "BanBot"
BOT_JID_RESOURCE = "bot@example.test/service"
MANUAL_BAN_REASON = "spam"
MANUAL_BAN_ACTOR = "manual_muc_ban"
MEMBER_AFFILIATION = "member"
ADMIN_AFFILIATION = "admin"
OWNER_AFFILIATION = "owner"
PARTICIPANT_ROLE = "participant"
MODERATOR_ROLE = "moderator"


class FakeMuc(TypedDict, total=False):
    """Typed fake MUC metadata mapping used by presence tests."""

    nick: str
    jid: str | None
    affiliation: str
    role: str
    status_codes: set[str]
    reason: str


class FakePresence:
    """Synthetic XMPP MUC presence stanza used by MucMixin tests."""

    def __init__(
        self,
        *,
        room: str = ROOM_JID,
        nick: str = USER_NICK,
        jid: str | None = USER_JID_RESOURCE,
        affiliation: str = MEMBER_AFFILIATION,
        role: str = PARTICIPANT_ROLE,
        status_codes: set[str] | None = None,
        reason: str | None = None,
    ) -> None:
        self._from = SimpleNamespace(bare=room, resource=nick)
        self._muc = FakeMuc(nick=nick, jid=jid, affiliation=affiliation, role=role)
        if status_codes is not None:
            self._muc["status_codes"] = status_codes
        if reason is not None:
            self._muc["reason"] = reason

        self.xml = ET.Element("presence")
        muc_user_node = ET.SubElement(self.xml, f"{MUC_USER_TAG}x")
        item = ET.SubElement(
            muc_user_node,
            f"{MUC_USER_TAG}item",
            {"affiliation": affiliation, "role": role},
        )
        if reason is not None:
            reason_el = ET.SubElement(item, f"{MUC_USER_TAG}reason")
            reason_el.text = reason
        for code in status_codes or set():
            ET.SubElement(muc_user_node, f"{MUC_USER_TAG}status", {"code": code})

    def __getitem__(self, key):
        if key == "from":
            return self._from
        if key == "muc":
            return self._muc
        raise KeyError(key)


class MucBotFixture(MucMixin, DatabaseMixin, CacheMixin):
    """Minimal bot fixture combining MUC, DB and cache mixins for tests."""

    def __init__(self, db_path: str | None = None):
        self.db_path = db_path
        self.protected_rooms = {ROOM_JID}
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
        self.rtbl_result = False
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
        room_occupants = self.occupants.get(room, {})
        info = room_occupants.get(nick) if nick else None
        if info is None and jid is not None:
            target_bare = self.bare_jid(jid)
            for occupant in room_occupants.values():
                occupant_jid = occupant.get("jid")
                if occupant_jid and self.bare_jid(occupant_jid) == target_bare:
                    info = occupant
                    break
        return bool(info and info.get("affiliation") in (ADMIN_AFFILIATION, OWNER_AFFILIATION))

    async def check_jid_against_rtbl(self, jid: str, nick: str) -> bool:
        """Record RTBL checks and return the configured fixture result."""
        self.rtbl_checks.append((jid, nick))
        return self.rtbl_result

    async def apply_ban_to_room(
        self,
        room: str,
        ban_jid: str | None,
        ban_nick: str | None,
        comment: str | None,
        issuer: str | None = None,
    ) -> None:
        """Record ban application arguments for test assertions."""
        self.applied.append((room, ban_jid, ban_nick, comment, issuer))

    async def verify_admin_rights(self, room: str) -> bool:
        return self.verify_result

    async def bot_send_message(self, **kwargs) -> None:
        self.sent.append(kwargs)

    async def maybe_auto_redact_after_manual_muc_ban(
        self,
        jid: str,
        comment: str,
        actor: str | None = None,
    ) -> None:
        self.manual_redactions.append((jid, comment, actor))

    async def unban_all(self, jid: str, issuer: str = "system") -> None:
        self.unbans.append((jid, issuer))


@pytest.mark.asyncio
async def test_muc_online_updates_occupants_and_applies_matching_jid_and_domain_bans(temp_db_path):
    bot = MucBotFixture(temp_db_path)
    await bot.setup_db()
    try:
        await bot.upsert_ban_db(USER_BARE_JID, USER_NICK, 0, "tester", "jid ban")
        await bot.upsert_ban_db("*.example.test", None, 0, "tester", "domain ban")
        await bot.load_bans_from_db()

        await bot.muc_online(
            FakePresence(
                room=ROOM_JID,
                nick=USER_NICK,
                jid=USER_JID_RESOURCE,
            )
        )

        assert bot.occupants[ROOM_JID][USER_NICK]["jid"] == USER_JID_RESOURCE
        assert bot.rtbl_checks == [(USER_JID_RESOURCE, USER_NICK)]
        assert (ROOM_JID, USER_BARE_JID, USER_NICK_NORMALIZED, "jid ban", None) in bot.applied
        assert (ROOM_JID, "*.example.test", None, "domain ban", None) in bot.applied
    finally:
        await bot.db.close()


@pytest.mark.asyncio
async def test_muc_online_converts_nick_only_ban_to_jid_ban(temp_db_path):
    bot = MucBotFixture(temp_db_path)
    await bot.setup_db()
    try:
        await bot.upsert_ban_db(None, BAD_NICK, 0, "tester", "nick ban")
        await bot.load_bans_from_db()

        await bot.muc_online(
            FakePresence(
                room=ROOM_JID,
                nick=BAD_NICK,
                jid=BAD_JID_RESOURCE,
            )
        )

        await bot.load_bans_from_db()
        assert BAD_BARE_JID in bot.ban_index_by_jid
        assert BAD_NICK_NORMALIZED not in bot.ban_index_by_nick
        assert any(call[1] == BAD_BARE_JID for call in bot.applied)
    finally:
        await bot.db.close()


@pytest.mark.asyncio
async def test_muc_offline_removes_occupant():
    bot = MucBotFixture()
    bot.occupants = {
        ROOM_JID: {
            USER_NICK: {"jid": USER_JID_RESOURCE, "affiliation": MEMBER_AFFILIATION, "role": PARTICIPANT_ROLE}
        }
    }

    await bot.muc_offline(FakePresence(room=ROOM_JID, nick=USER_NICK))

    assert USER_NICK not in bot.occupants[ROOM_JID]


@pytest.mark.asyncio
async def test_muc_offline_recovers_manual_ban_and_auto_redacts(temp_db_path):
    bot = MucBotFixture(temp_db_path)
    await bot.setup_db()
    bot.occupants = {
        ROOM_JID: {
            USER_NICK: {"jid": USER_JID_RESOURCE, "affiliation": MEMBER_AFFILIATION, "role": PARTICIPANT_ROLE}
        }
    }

    try:
        await bot.muc_offline(
            FakePresence(
                room=ROOM_JID,
                nick=USER_NICK,
                status_codes={"301"},
                reason=MANUAL_BAN_REASON,
            )
        )

        await bot.load_bans_from_db()
        assert USER_BARE_JID in bot.ban_index_by_jid
        assert bot.ban_index_by_jid[USER_BARE_JID][4] == MANUAL_BAN_REASON
        assert bot.manual_redactions == [(USER_BARE_JID, MANUAL_BAN_REASON, MANUAL_BAN_ACTOR)]
    finally:
        await bot.db.close()


@pytest.mark.asyncio
async def test_muc_offline_recovers_manual_ban_reason_from_xml(temp_db_path):
    bot = MucBotFixture(temp_db_path)
    await bot.setup_db()
    bot.occupants = {
        ROOM_JID: {
            USER_NICK: {"jid": USER_JID_RESOURCE, "affiliation": MEMBER_AFFILIATION, "role": PARTICIPANT_ROLE}
        }
    }

    try:
        presence = FakePresence(
            room=ROOM_JID,
            nick=USER_NICK,
            status_codes={"301"},
            reason=None,
        )
        reason_el = presence.xml.find(f".//{MUC_USER_TAG}reason")
        if reason_el is None:
            item = presence.xml.find(f".//{MUC_USER_TAG}item")
            if item is None:
                pytest.fail("Malformed FakePresence XML: missing muc#user item element")
            reason_el = ET.SubElement(item, f"{MUC_USER_TAG}reason")
        reason_el.text = MANUAL_BAN_REASON

        await bot.muc_offline(presence)

        await bot.load_bans_from_db()
        assert bot.ban_index_by_jid[USER_BARE_JID][4] == MANUAL_BAN_REASON
        assert bot.manual_redactions == [(USER_BARE_JID, MANUAL_BAN_REASON, MANUAL_BAN_ACTOR)]
    finally:
        await bot.db.close()


@pytest.mark.asyncio
async def test_on_muc_presence_warns_when_bot_loses_admin(monkeypatch):
    import banbot.muc as muc_module

    monkeypatch.setattr(muc_module, "ADMIN_ROOM", ADMIN_ROOM_JID)
    monkeypatch.setattr(muc_module, "NICK", BOT_NICK)

    bot = MucBotFixture()
    bot.bot_admin_state[ROOM_JID] = True
    bot.room_join_time[ROOM_JID] = time.time() - 10
    bot.verify_result = False
    bot.occupants[ROOM_JID] = {
        BOT_NICK: {
            "jid": BOT_JID_RESOURCE,
            "affiliation": ADMIN_AFFILIATION,
            "role": MODERATOR_ROLE,
        }
    }

    await bot.on_muc_presence(
        FakePresence(
            room=ROOM_JID,
            nick=BOT_NICK,
            jid=BOT_JID_RESOURCE,
            affiliation=MEMBER_AFFILIATION,
            role=PARTICIPANT_ROLE,
        )
    )

    assert bot.bot_admin_state[ROOM_JID] is False
    assert "lost admin/owner rights" in bot.sent[-1]["mbody"]
    presence_state = bot.occupants[ROOM_JID][BOT_NICK]
    assert presence_state["jid"] == BOT_JID_RESOURCE
    assert presence_state["affiliation"] == MEMBER_AFFILIATION
    assert presence_state["role"] == PARTICIPANT_ROLE


@pytest.mark.asyncio
async def test_on_muc_presence_warns_when_admin_room_state_only_exists_in_occupant_cache(monkeypatch):
    import banbot.muc as muc_module

    monkeypatch.setattr(muc_module, "ADMIN_ROOM", ADMIN_ROOM_JID)
    monkeypatch.setattr(muc_module, "NICK", BOT_NICK)

    bot = MucBotFixture()
    bot.room_join_time[ADMIN_ROOM_JID] = time.time() - 10
    bot.verify_result = False

    # Simulate status-visible admin rights from the occupant cache, but no
    # initialized bot_admin_state yet.
    bot.occupants[ADMIN_ROOM_JID] = {
        BOT_NICK: {
            "jid": BOT_JID_RESOURCE,
            "affiliation": ADMIN_AFFILIATION,
            "role": MODERATOR_ROLE,
        }
    }

    await bot.on_muc_presence(
        FakePresence(
            room=ADMIN_ROOM_JID,
            nick=BOT_NICK,
            jid=BOT_JID_RESOURCE,
            affiliation=MEMBER_AFFILIATION,
            role=PARTICIPANT_ROLE,
        )
    )

    assert bot.bot_admin_state[ADMIN_ROOM_JID] is False
    assert bot.sent[-1]["mto"] == ADMIN_ROOM_JID
    assert "lost admin/owner rights in admin room admin@conference.example.test" in bot.sent[-1]["mbody"]
    presence_state = bot.occupants[ADMIN_ROOM_JID][BOT_NICK]
    assert presence_state["jid"] == BOT_JID_RESOURCE
    assert presence_state["affiliation"] == MEMBER_AFFILIATION
    assert presence_state["role"] == PARTICIPANT_ROLE


@pytest.mark.asyncio
async def test_on_disconnect_clears_runtime_state_and_schedules_reconnect(monkeypatch):
    bot = MucBotFixture()
    bot.occupants = {"room": {USER_NICK: {}}}
    bot.bot_admin_state = {"room": True}
    bot.room_join_time = {"room": 123.0}

    async def fake_reconnect():
        pass

    monkeypatch.setattr(bot, "_delayed_reconnect", fake_reconnect)
    await bot.on_disconnect(None)

    assert bot.reconnecting is True
    assert bot.occupants == {}
    assert bot.bot_admin_state == {}
    assert bot.room_join_time == {}
    assert bot.reconnect_task is not None
    done, pending = await asyncio.wait({bot.reconnect_task}, timeout=1)
    assert pending == set()
    assert bot.reconnect_task in done
    assert bot.reconnect_task.done()


@pytest.mark.asyncio
async def test_on_disconnect_does_not_schedule_overlapping_reconnects(monkeypatch):
    bot = MucBotFixture()
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
    await asyncio.sleep(0)
    assert calls["count"] == 1

    blocker.set()
    done, pending = await asyncio.wait({first_task}, timeout=1)
    assert pending == set()
    assert first_task in done
    assert first_task.done()


@pytest.mark.asyncio
async def test_delayed_reconnect_waits_for_session_start_signal(monkeypatch):
    import banbot.muc as muc_module

    bot = MucBotFixture()
    bot.reconnecting = True
    calls = {"connect": 0}

    async def no_sleep(_delay):
        pass

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

    bot = MucBotFixture()
    bot.reconnecting = True
    calls = {"connect": 0}

    async def no_sleep(_delay):
        pass

    async def short_wait_for(awaitable, timeout):
        # This test double may receive a coroutine that we intentionally do not
        # await while simulating timeout behavior; close it to avoid coroutine
        # warnings and release its frame/resources.
        close = getattr(awaitable, "close", None)
        if callable(close):
            close()
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
