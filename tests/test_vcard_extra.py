"""vCard/avatar tests with mocked Slixmpp plugins."""

from __future__ import annotations

import hashlib

import pytest

from banbot.vcard import VCardMixin


class FakeVCard(dict):
    def __init__(self):
        super().__init__()
        self["PHOTO"] = {}
        self["ORG"] = {}


class FakeXep0054:
    def __init__(self):
        self.published = []

    def make_vcard(self):
        return FakeVCard()

    async def publish_vcard(self, vcard):
        self.published.append(vcard)


class FakeXep0084:
    def __init__(self):
        self.avatars = []

    async def publish_avatar(self, data):
        self.avatars.append(data)


class VCardBot(VCardMixin):
    def __init__(self):
        self.xep0054 = FakeXep0054()
        self.xep0084 = FakeXep0084()
        self.sent = []

    def __getitem__(self, key):
        if key == "xep_0054":
            return self.xep0054
        if key == "xep_0084":
            return self.xep0084
        raise KeyError(key)

    def send(self, stanza):
        self.sent.append(stanza)


@pytest.mark.asyncio
async def test_update_vcard_publishes_fields_avatar_and_avatar_hash(tmp_path, monkeypatch):
    import banbot.vcard as vcard_module
    import config

    avatar = tmp_path / "avatar.png"
    avatar_data = b"fake-png-data"
    avatar.write_bytes(avatar_data)

    monkeypatch.setattr(config, "AVATAR_PATH", str(avatar), raising=False)
    monkeypatch.setattr(config, "VCARD_NICKNAME", "BanBot", raising=False)
    monkeypatch.setattr(config, "VCARD_FN", "Ban Management Bot", raising=False)
    monkeypatch.setattr(config, "VCARD_ORG", "envs", raising=False)
    monkeypatch.setattr(config, "VCARD_ROLE", "moderator", raising=False)
    monkeypatch.setattr(config, "VCARD_URL", "https://envs.net", raising=False)
    monkeypatch.setattr(config, "VCARD_NOTE", "test note", raising=False)
    monkeypatch.setattr(vcard_module.asyncio, "sleep", lambda delay: _completed_sleep())

    bot = VCardBot()
    assert await bot.update_vcard() is True

    vcard = bot.xep0054.published[0]
    assert vcard["PHOTO"]["TYPE"] == "image/png"
    assert vcard["PHOTO"]["BINVAL"] == avatar_data
    assert vcard["NICKNAME"] == "BanBot"
    assert vcard["FN"] == "Ban Management Bot"
    assert vcard["ORG"]["ORGNAME"] == "envs"
    assert vcard["ROLE"] == "moderator"
    assert vcard["URL"] == "https://envs.net"
    assert vcard["NOTE"] == "test note"
    assert bot.xep0084.avatars == [avatar_data]

    presence_xml = bot.sent[0].xml
    expected_hash = hashlib.sha1(avatar_data).hexdigest()
    assert presence_xml.find(".//{vcard-temp:x:update}x/photo").text == expected_hash


@pytest.mark.asyncio
async def test_update_vcard_without_avatar_only_publishes_vcard(monkeypatch):
    import config

    monkeypatch.setattr(config, "AVATAR_PATH", None, raising=False)
    for attr in ("VCARD_NICKNAME", "VCARD_FN", "VCARD_ORG", "VCARD_ROLE", "VCARD_URL", "VCARD_NOTE"):
        monkeypatch.setattr(config, attr, "", raising=False)

    bot = VCardBot()
    assert await bot.update_vcard() is True

    assert len(bot.xep0054.published) == 1
    assert bot.xep0084.avatars == []
    assert bot.sent == []


async def _completed_sleep():
    return None
