"""vCard/avatar tests with mocked Slixmpp plugins."""

from __future__ import annotations

import hashlib

import pytest

from banbot.vcard import VCardMixin


VCARD_CONFIG_ATTRS = (
    "VCARD_NICKNAME",
    "VCARD_FN",
    "VCARD_ORG",
    "VCARD_ROLE",
    "VCARD_URL",
    "VCARD_NOTE",
)


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


@pytest.fixture
def completed_sleep_mock():
    """Return an async no-op replacement for asyncio.sleep used by vCard tests."""

    async def _completed_sleep(*args, **kwargs):
        pass

    return _completed_sleep


@pytest.fixture
def cleared_vcard_config(monkeypatch):
    """Clear vCard-related config values for tests that need an empty profile."""
    import config

    monkeypatch.setattr(config, "AVATAR_PATH", None, raising=False)
    for attr in VCARD_CONFIG_ATTRS:
        monkeypatch.setattr(config, attr, "", raising=False)


def set_complete_vcard_config(monkeypatch) -> None:
    """Configure all optional vCard profile fields with representative values."""
    import config

    set_complete_vcard_config(monkeypatch)


@pytest.mark.asyncio
async def test_update_vcard_publishes_fields_avatar_and_avatar_hash(tmp_path, monkeypatch, completed_sleep_mock):
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
    monkeypatch.setattr("asyncio.sleep", completed_sleep_mock)

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
async def test_update_vcard_without_avatar_only_publishes_vcard(cleared_vcard_config):
    bot = VCardBot()
    assert await bot.update_vcard() is True

    assert len(bot.xep0054.published) == 1
    vcard = bot.xep0054.published[0]

    # No avatar configured: keep PHOTO empty and do not publish XEP-0084/avatar-hash presence.
    assert vcard["PHOTO"] == {}
    assert bot.xep0084.avatars == []
    assert bot.sent == []

    # No profile fields configured: values should remain absent/empty.
    for field in ("NICKNAME", "FN", "ROLE", "URL", "NOTE"):
        assert field not in vcard or vcard[field] == ""

    # ORG is preinitialized by FakeVCard and should remain empty.
    assert vcard["ORG"] == {}


@pytest.mark.asyncio
async def test_update_vcard_skips_presence_when_disconnected(
    tmp_path,
    monkeypatch,
    caplog,
    completed_sleep_mock,
    cleared_vcard_config,
):
    import config

    avatar = tmp_path / "avatar.png"
    avatar_data = b"fake-png-data"
    avatar.write_bytes(avatar_data)

    monkeypatch.setattr(config, "AVATAR_PATH", str(avatar), raising=False)
    monkeypatch.setattr("asyncio.sleep", completed_sleep_mock)

    bot = VCardBot(connected=False)

    with caplog.at_level("DEBUG", logger="banbot.vcard"):
        assert await bot.update_vcard() is True

    assert len(bot.xep0054.published) == 1
    vcard = bot.xep0054.published[0]
    assert vcard["PHOTO"]["TYPE"] == "image/png"
    assert vcard["PHOTO"]["BINVAL"] == avatar_data
    assert bot.xep0084.avatars == [avatar_data]
    assert bot.sent == []
    assert "Skipping XEP-0153 avatar hash presence" in caplog.text


@pytest.mark.asyncio
async def test_update_vcard_continues_when_avatar_publish_fails(
    tmp_path,
    monkeypatch,
    caplog,
    completed_sleep_mock,
    cleared_vcard_config,
):
    """Verify XEP-0084 avatar publish failures are non-fatal."""
    import config

    avatar = tmp_path / "avatar.png"
    avatar_data = b"fake-png-data"
    avatar.write_bytes(avatar_data)

    monkeypatch.setattr(config, "AVATAR_PATH", str(avatar), raising=False)
    monkeypatch.setattr("asyncio.sleep", completed_sleep_mock)

    bot = VCardBot(connected=True)

    async def failing_publish_avatar(data):
        raise RuntimeError("publish failed")

    monkeypatch.setattr(bot.xep0084, "publish_avatar", failing_publish_avatar)

    with caplog.at_level("WARNING", logger="banbot.vcard"):
        assert await bot.update_vcard() is True

    assert len(bot.xep0054.published) == 1
    vcard = bot.xep0054.published[0]
    assert vcard["PHOTO"]["TYPE"] == "image/png"
    assert vcard["PHOTO"]["BINVAL"] == avatar_data
    assert bot.xep0084.avatars == []
    assert "Failed to update XEP-0084 avatar" in caplog.text


@pytest.mark.asyncio
async def test_update_vcard_skips_presence_when_connection_lost_after_publish(
    tmp_path,
    monkeypatch,
    completed_sleep_mock,
    cleared_vcard_config,
):
    """Verify presence is skipped if connection is lost after avatar publishing."""
    import config

    avatar = tmp_path / "avatar.png"
    avatar_data = b"fake-png-data"
    avatar.write_bytes(avatar_data)

    monkeypatch.setattr(config, "AVATAR_PATH", str(avatar), raising=False)
    monkeypatch.setattr("asyncio.sleep", completed_sleep_mock)

    bot = VCardBot(connected=True)

    async def publish_avatar_and_disconnect(data):
        bot.xep0084.avatars.append(data)
        bot.connected = False

    monkeypatch.setattr(bot.xep0084, "publish_avatar", publish_avatar_and_disconnect)

    assert await bot.update_vcard() is True

    assert len(bot.xep0054.published) == 1
    vcard = bot.xep0054.published[0]
    assert vcard["PHOTO"]["TYPE"] == "image/png"
    assert vcard["PHOTO"]["BINVAL"] == avatar_data
    assert bot.xep0084.avatars == [avatar_data]
    assert bot.sent == []
