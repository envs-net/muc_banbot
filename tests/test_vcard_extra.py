"""vCard/avatar tests with mocked Slixmpp plugins."""

from __future__ import annotations

import importlib
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
    def __init__(self, connected=True):
        self.xep0054 = FakeXep0054()
        self.xep0084 = FakeXep0084()
        self.sent = []
        self.connected = connected

    def __getitem__(self, key):
        if key == "xep_0054":
            return self.xep0054
        if key == "xep_0084":
            return self.xep0084
        raise KeyError(key)

    def send(self, stanza):
        self.sent.append(stanza)

    def is_connected(self):
        return self.connected


@pytest.mark.asyncio
async def test_update_vcard_publishes_fields_avatar_and_avatar_hash(tmp_path, monkeypatch):
    vcard_module = importlib.import_module("banbot.vcard")
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
    monkeypatch.setattr(vcard_module.asyncio, "sleep", _completed_sleep)

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


@pytest.mark.asyncio
async def test_update_vcard_skips_avatar_hash_presence_without_active_stream(
    tmp_path,
    monkeypatch,
    caplog,
):
    vcard_module = importlib.import_module("banbot.vcard")
    import config

    avatar = tmp_path / "avatar.png"
    avatar.write_bytes(b"fake-png-data")

    monkeypatch.setattr(config, "AVATAR_PATH", str(avatar), raising=False)
    for attr in ("VCARD_NICKNAME", "VCARD_FN", "VCARD_ORG", "VCARD_ROLE", "VCARD_URL", "VCARD_NOTE"):
        monkeypatch.setattr(config, attr, "", raising=False)
    monkeypatch.setattr(vcard_module.asyncio, "sleep", _completed_sleep)

    bot = VCardBot(connected=False)

    with caplog.at_level("DEBUG", logger="banbot.vcard"):
        assert await bot.update_vcard() is True

    assert len(bot.xep0054.published) == 1
    assert bot.xep0084.avatars == [b"fake-png-data"]
    assert bot.sent == []
    assert "Skipping XEP-0153 avatar hash presence" in caplog.text


def test_send_avatar_hash_presence_returns_false_without_active_stream():
    bot = VCardBot(connected=False)

    assert bot._send_avatar_hash_presence("abc123") is False
    assert bot.sent == []


def test_send_avatar_hash_presence_sends_when_stream_is_active():
    bot = VCardBot(connected=True)

    assert bot._send_avatar_hash_presence("abc123") is True

    presence_xml = bot.sent[0].xml
    assert presence_xml.find(".//{vcard-temp:x:update}x/photo").text == "abc123"


async def _completed_sleep(*args, **kwargs):
    return None
