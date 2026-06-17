"""Additional room-level moderation apply/unapply tests."""

from __future__ import annotations

import importlib
import asyncio

import pytest

pytest.importorskip("slixmpp")

from banbot.moderation import ModerationMixin
from banbot.utils import bare_jid


class FakeMucPlugin:
    def __init__(self):
        self.affiliations = []
        self.roles = []

    async def set_affiliation(self, **kwargs):
        self.affiliations.append(kwargs)

    async def set_role(self, **kwargs):
        self.roles.append(kwargs)


class ApplyRoomBot(ModerationMixin):
    def __init__(self):
        self.protected_rooms = {"room@conference.example.test"}
        self.occupants = {
            "room@conference.example.test": {
                "BanBot": {"jid": "bot@example.test/res", "affiliation": "admin", "role": "moderator"},
                "User": {"jid": "user@example.test/res", "affiliation": "member", "role": "participant"},
                "Admin": {"jid": "admin@example.test/res", "affiliation": "admin", "role": "moderator"},
                "DomainUser": {"jid": "bad@spam.example/res", "affiliation": "member", "role": "participant"},
            }
        }
        self.plugin = {"xep_0045": FakeMucPlugin()}
        self.muc_write_semaphore = asyncio.Semaphore(10)
        self.allow_user_cmds = True
        self.show_ban_in_muc = True
        self.sent = []

    def bare_jid(self, jid):
        return bare_jid(jid)

    def is_bot_admin_or_owner(self, room):
        info = self.occupants.get(room, {}).get("BanBot")
        return bool(info and info.get("affiliation") in ("owner", "admin"))

    async def bot_send_message(self, **kwargs):
        self.sent.append(kwargs)

    async def notify_protected(self, room, message):
        await self.bot_send_message(mto=room, mbody=message, mtype="groupchat")


@pytest.mark.asyncio
async def test_apply_ban_to_room_sets_outcast_and_kicks_matching_non_admin(monkeypatch):
    moderation_module = importlib.import_module("banbot.moderation")

    monkeypatch.setattr(moderation_module, "ADMIN_ROOM", "admin@conference.example.test")
    bot = ApplyRoomBot()

    await bot.apply_ban_to_room(
        "room@conference.example.test",
        "user@example.test",
        "User",
        "spam",
        issuer="admin@example.test",
    )

    muc = bot.plugin["xep_0045"]
    assert muc.affiliations[0]["jid"] == "user@example.test"
    assert muc.affiliations[0]["affiliation"] == "outcast"
    assert {call["nick"] for call in muc.roles} == {"User"}
    assert "Banned User" in bot.sent[-1]["mbody"]


@pytest.mark.asyncio
async def test_apply_ban_to_room_skips_admin_kick_and_reports_missing_bot_rights(monkeypatch):
    moderation_module = importlib.import_module("banbot.moderation")

    monkeypatch.setattr(moderation_module, "ADMIN_ROOM", "admin@conference.example.test")
    bot = ApplyRoomBot()
    bot.occupants["room@conference.example.test"]["BanBot"]["affiliation"] = "member"

    await bot.apply_ban_to_room(
        "room@conference.example.test",
        "admin@example.test",
        "Admin",
        "spam",
        issuer="admin@example.test",
    )

    assert bot.plugin["xep_0045"].affiliations == []
    assert bot.plugin["xep_0045"].roles == []
    assert "missing admin/owner rights" in bot.sent[-1]["mbody"]


@pytest.mark.asyncio
async def test_apply_domain_ban_kicks_matching_domain_only(monkeypatch):
    moderation_module = importlib.import_module("banbot.moderation")

    monkeypatch.setattr(moderation_module, "ADMIN_ROOM", "admin@conference.example.test")
    bot = ApplyRoomBot()

    await bot.apply_ban_to_room(
        "room@conference.example.test",
        "*.spam.example",
        None,
        "domain spam",
        issuer="admin@example.test",
    )

    roles = bot.plugin["xep_0045"].roles
    assert {call["nick"] for call in roles} == {"DomainUser"}
    assert bot.plugin["xep_0045"].affiliations == []


@pytest.mark.asyncio
async def test_apply_unban_to_room_removes_outcast_and_restores_online_user(monkeypatch):
    moderation_module = importlib.import_module("banbot.moderation")

    monkeypatch.setattr(moderation_module, "ADMIN_ROOM", "admin@conference.example.test")
    bot = ApplyRoomBot()

    await bot.apply_unban_to_room(
        "room@conference.example.test",
        "user@example.test",
        "User",
    )

    muc = bot.plugin["xep_0045"]
    assert muc.affiliations[0]["jid"] == "user@example.test"
    assert muc.affiliations[0]["affiliation"] == "none"
    assert any(call["nick"] == "User" and call["role"] == "participant" for call in muc.roles)
