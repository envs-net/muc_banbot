"""vCard/avatar tests with mocked Slixmpp plugins."""

from __future__ import annotations

import asyncio
import hashlib

import pytest

pytest.importorskip("slixmpp")

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
        self.metadata = []
        self.stop_calls = 0

    async def publish_avatar(self, data):
        self.avatars.append(data)

    async def publish_avatar_metadata(self, metadata):
        self.metadata.append(metadata)

    async def stop(self):
        self.stop_calls += 1


class FakeXep0153:
    def __init__(self):
        self.hashes = []
        self.api = {"set_hash": self.set_hash}

    async def set_hash(self, jid, *, args):
        self.hashes.append((jid, args))


class FakeAvatarUpdate:
    def __init__(self, presence):
        self.presence = presence

    def __setitem__(self, key, value):
        assert key == "photo"
        x = self.presence.xml.find("{vcard-temp:x:update}x")
        if x is None:
            x = __import__("xml.etree.ElementTree", fromlist=["ElementTree"]).SubElement(
                self.presence.xml, "{vcard-temp:x:update}x"
            )
        photo = x.find("photo")
        if photo is None:
            photo = __import__("xml.etree.ElementTree", fromlist=["ElementTree"]).SubElement(
                x, "photo"
            )
        photo.text = value


class FakePresence:
    def __init__(self, bot, kwargs):
        from xml.etree import ElementTree as ET

        self.bot = bot
        self.stream = bot
        self.kwargs = dict(kwargs)
        self.xml = ET.Element("presence")
        if kwargs.get("pto") is not None:
            self.xml.set("to", str(kwargs["pto"]))

    def __getitem__(self, key):
        if key == "vcard_temp_update":
            return FakeAvatarUpdate(self)
        if key == "to":
            return self.xml.get("to", "")
        return self.kwargs.get(key, "")

    def send(self):
        self.bot.sent.append(self)


class VCardBot(VCardMixin):
    def __init__(self, connected=True):
        self.xep0054 = FakeXep0054()
        self.xep0084 = FakeXep0084()
        self.xep0153 = FakeXep0153()
        self.sent = []
        self.connected = connected
        self.boundjid = type(
            "BoundJID",
            (),
            {
                "bare": "bot@example.org",
                "full": "bot@example.org/tests",
            },
        )()
        self.room_bot_nicks = {}
        self.avatar_hash = None

    def __getitem__(self, key):
        if key == "xep_0054":
            return self.xep0054
        if key == "xep_0084":
            return self.xep0084
        if key == "xep_0153":
            return self.xep0153
        raise KeyError(key)

    def make_presence(self, **kwargs):
        return FakePresence(self, kwargs)

    def is_connected(self):
        return self.connected


@pytest.fixture
def no_op_sleep_mock(monkeypatch):
    """Patch asyncio.sleep with an async no-op replacement for vCard tests."""

    async def _completed_sleep(*args, **kwargs):
        pass

    monkeypatch.setattr("asyncio.sleep", _completed_sleep)
    yield


@pytest.fixture
def cleared_vcard_config(monkeypatch):
    """Clear vCard-related config values for tests that need an empty profile."""
    import config

    monkeypatch.setattr(config, "AVATAR_PATH", None, raising=False)
    for attr in VCARD_CONFIG_ATTRS:
        monkeypatch.setattr(config, attr, "", raising=False)
    yield


@pytest.fixture
def set_complete_vcard_config(monkeypatch):
    """Configure all optional vCard profile fields with representative values."""
    import config

    monkeypatch.setattr(config, "VCARD_NICKNAME", "BanBot", raising=False)
    monkeypatch.setattr(config, "VCARD_FN", "Ban Management Bot", raising=False)
    monkeypatch.setattr(config, "VCARD_ORG", "envs", raising=False)
    monkeypatch.setattr(config, "VCARD_ROLE", "moderator", raising=False)
    monkeypatch.setattr(config, "VCARD_URL", "https://envs.net", raising=False)
    monkeypatch.setattr(config, "VCARD_NOTE", "test note", raising=False)
    yield


def test_set_complete_vcard_config_sets_all_fields(set_complete_vcard_config):
    import config

    assert config.VCARD_NICKNAME == "BanBot"
    assert config.VCARD_FN == "Ban Management Bot"
    assert config.VCARD_ORG == "envs"
    assert config.VCARD_ROLE == "moderator"
    assert config.VCARD_URL == "https://envs.net"
    assert config.VCARD_NOTE == "test note"


@pytest.mark.asyncio
async def test_load_avatar_payload_falls_back_to_packaged_default(monkeypatch, tmp_path):
    import config
    from banbot import bundled_assets

    packaged = tmp_path / "packaged"
    packaged.mkdir()
    avatar = packaged / "avatar.png"
    avatar.write_bytes(b"packaged-avatar")

    working = tmp_path / "working"
    working.mkdir()
    monkeypatch.chdir(working)
    monkeypatch.setattr(bundled_assets, "_BUNDLED_DIR", packaged)
    monkeypatch.setattr(config, "AVATAR_PATH", "avatar.png", raising=False)

    payload = await VCardBot()._load_avatar_payload("avatar.png")

    assert payload is not None
    assert payload.data == b"packaged-avatar"
    assert payload.media_type == "image/png"


@pytest.mark.asyncio
async def test_update_vcard_with_complete_profile_and_avatar(
    tmp_path,
    monkeypatch,
    no_op_sleep_mock,
    set_complete_vcard_config,
):
    import config

    avatar = tmp_path / "avatar.png"
    avatar_data = b"fake-png-data"
    avatar.write_bytes(avatar_data)

    monkeypatch.setattr(config, "AVATAR_PATH", str(avatar), raising=False)
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
    expected_hash = hashlib.sha1(avatar_data).hexdigest()
    assert bot.xep0084.avatars == [avatar_data]
    assert bot.xep0084.metadata == [[{
        "id": expected_hash,
        "type": "image/png",
        "bytes": len(avatar_data),
    }]]
    assert bot.xep0153.hashes == [(bot.boundjid, expected_hash)]

    assert len(bot.sent) == 1
    presence_xml = bot.sent[0].xml
    photo_element = presence_xml.find(".//{vcard-temp:x:update}x/photo")
    assert photo_element is not None
    assert photo_element.text == expected_hash
    assert bot.sent[0].kwargs["pfrom"] == "bot@example.org/tests"


@pytest.mark.asyncio
async def test_update_vcard_broadcasts_avatar_hash_to_joined_mucs(
    tmp_path,
    monkeypatch,
    cleared_vcard_config,
):
    import config

    avatar = tmp_path / "avatar.jpg"
    avatar_data = b"fake-jpeg-data"
    avatar.write_bytes(avatar_data)
    monkeypatch.setattr(config, "AVATAR_PATH", str(avatar), raising=False)

    bot = VCardBot()
    bot.room_bot_nicks = {
        "room-a@example.org": "BanBot",
        "room-b@example.org": "OtherNick",
    }

    assert await bot.update_vcard() is True

    expected_hash = hashlib.sha1(avatar_data).hexdigest()
    assert [presence.kwargs.get("pto") for presence in bot.sent] == [
        None,
        "room-a@example.org/BanBot",
        "room-b@example.org/OtherNick",
    ]
    for presence in bot.sent:
        photo = presence.xml.find(".//{vcard-temp:x:update}x/photo")
        assert photo is not None
        assert photo.text == expected_hash


@pytest.mark.asyncio
async def test_update_vcard_without_avatar_withdraws_cached_avatar(cleared_vcard_config):
    bot = VCardBot()
    bot.avatar_hash = "old-hash"

    assert await bot.update_vcard() is True

    assert len(bot.xep0054.published) == 1
    vcard = bot.xep0054.published[0]

    # Explicitly disabling the avatar withdraws both modern metadata and the
    # legacy cached hash rather than leaving clients on the old image.
    assert vcard["PHOTO"] == {}
    assert bot.xep0084.avatars == []
    assert bot.xep0084.metadata == []
    assert bot.xep0084.stop_calls == 1
    assert bot.xep0153.hashes == [(bot.boundjid, "")]
    assert len(bot.sent) == 1
    photo = bot.sent[0].xml.find(".//{vcard-temp:x:update}x/photo")
    assert photo is not None
    assert photo.text in (None, "")
    assert bot.avatar_hash is None

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
    no_op_sleep_mock,
    cleared_vcard_config,
):
    import config

    avatar = tmp_path / "avatar.png"
    avatar_data = b"fake-png-data"
    avatar.write_bytes(avatar_data)

    monkeypatch.setattr(config, "AVATAR_PATH", str(avatar), raising=False)
    bot = VCardBot(connected=False)

    with caplog.at_level("DEBUG", logger="banbot.vcard"):
        assert await bot.update_vcard() is True

    assert len(bot.xep0054.published) == 1
    vcard = bot.xep0054.published[0]
    assert vcard["PHOTO"]["TYPE"] == "image/png"
    assert vcard["PHOTO"]["BINVAL"] == avatar_data
    assert bot.xep0084.avatars == [avatar_data]
    assert bot.sent == []

    debug_messages = [
        record.getMessage()
        for record in caplog.records
        if record.name == "banbot.vcard" and record.levelname == "DEBUG"
    ]
    assert debug_messages == [
        "Skipping XEP-0153 avatar hash presence because XMPP stream is not connected"
    ]


@pytest.mark.asyncio
async def test_update_vcard_continues_when_avatar_publish_fails(
    tmp_path,
    monkeypatch,
    caplog,
    no_op_sleep_mock,
    cleared_vcard_config,
):
    """Verify XEP-0084 avatar publish failures are non-fatal."""
    import config

    avatar = tmp_path / "avatar.png"
    avatar_data = b"fake-png-data"
    avatar.write_bytes(avatar_data)

    monkeypatch.setattr(config, "AVATAR_PATH", str(avatar), raising=False)
    bot = VCardBot(connected=True)

    async def mock_failing_publish_avatar(data):
        raise RuntimeError("publish failed")

    monkeypatch.setattr(bot.xep0084, "publish_avatar", mock_failing_publish_avatar)

    with caplog.at_level("WARNING", logger="banbot.vcard"):
        assert await bot.update_vcard() is True

    assert len(bot.xep0054.published) == 1
    vcard = bot.xep0054.published[0]
    assert vcard["PHOTO"]["TYPE"] == "image/png"
    assert vcard["PHOTO"]["BINVAL"] == avatar_data
    assert bot.xep0084.avatars == []

    warning_messages = [
        record.getMessage()
        for record in caplog.records
        if record.name == "banbot.vcard" and record.levelname == "WARNING"
    ]
    assert "⚠️ Failed to update XEP-0084 avatar: publish failed" in warning_messages


@pytest.mark.asyncio
async def test_update_vcard_skips_presence_when_connection_lost_after_publish(
    tmp_path,
    monkeypatch,
    caplog,
    no_op_sleep_mock,
    cleared_vcard_config,
):
    """Verify presence is skipped if connection is lost after avatar publishing."""
    import config

    avatar = tmp_path / "avatar.png"
    avatar_data = b"fake-png-data"
    avatar.write_bytes(avatar_data)

    monkeypatch.setattr(config, "AVATAR_PATH", str(avatar), raising=False)
    bot = VCardBot(connected=True)

    async def mock_publish_avatar_and_disconnect(data):
        bot.xep0084.avatars.append(data)
        bot.connected = False

    monkeypatch.setattr(bot.xep0084, "publish_avatar", mock_publish_avatar_and_disconnect)

    with caplog.at_level("DEBUG", logger="banbot.vcard"):
        assert await bot.update_vcard() is True

    assert len(bot.xep0054.published) == 1
    vcard = bot.xep0054.published[0]
    assert vcard["PHOTO"]["TYPE"] == "image/png"
    assert vcard["PHOTO"]["BINVAL"] == avatar_data
    assert bot.xep0084.avatars == [avatar_data]
    assert bot.sent == []

    debug_messages = [
        record.getMessage()
        for record in caplog.records
        if record.name == "banbot.vcard" and record.levelname == "DEBUG"
    ]
    assert debug_messages == [
        "Skipping XEP-0153 avatar hash presence because XMPP stream is not connected"
    ]

@pytest.mark.asyncio
async def test_missing_configured_avatar_does_not_erase_published_identity(
    tmp_path,
    monkeypatch,
    cleared_vcard_config,
):
    import config

    missing = tmp_path / "missing-avatar.png"
    monkeypatch.setattr(config, "AVATAR_PATH", str(missing), raising=False)

    bot = VCardBot()
    bot.avatar_hash = "old-hash"

    assert await bot.update_vcard() is False
    assert bot.xep0054.published == []
    assert bot.xep0084.stop_calls == 0
    assert bot.xep0153.hashes == []
    assert bot.sent == []
    assert bot.avatar_hash == "old-hash"


@pytest.mark.asyncio
async def test_xep0054_failure_preserves_previous_legacy_hash(
    tmp_path,
    monkeypatch,
    cleared_vcard_config,
):
    import config

    avatar = tmp_path / "avatar.png"
    avatar.write_bytes(b"new-avatar")
    monkeypatch.setattr(config, "AVATAR_PATH", str(avatar), raising=False)

    bot = VCardBot()
    bot.avatar_hash = "old-hash"

    async def fail_vcard(_vcard):
        raise RuntimeError("vCard unavailable")

    monkeypatch.setattr(bot.xep0054, "publish_vcard", fail_vcard)

    assert await bot.update_vcard() is False
    # XEP-0084 is independent and may still publish successfully.
    assert bot.xep0084.avatars == [b"new-avatar"]
    # XEP-0153 must keep the last published/cache-aligned value because the
    # matching XEP-0054 PHOTO was not accepted by the server.
    assert bot.xep0153.hashes == []
    assert bot.sent == []
    assert bot.avatar_hash == "old-hash"


@pytest.mark.asyncio
async def test_avatar_presence_failure_isolated_per_target(
    tmp_path,
    monkeypatch,
    cleared_vcard_config,
):
    import config

    avatar = tmp_path / "avatar.png"
    avatar.write_bytes(b"avatar")
    monkeypatch.setattr(config, "AVATAR_PATH", str(avatar), raising=False)

    bot = VCardBot()
    bot.room_bot_nicks = {
        "broken@example.org": "BanBot",
        "working@example.org": "BanBot",
    }
    original_make = bot._make_avatar_hash_presence

    def make_presence(avatar_hash, *, pto=None):
        presence = original_make(avatar_hash, pto=pto)
        if presence is not None and pto == "broken@example.org/BanBot":
            def fail_send():
                raise RuntimeError("room presence failed")

            presence.send = fail_send
        return presence

    monkeypatch.setattr(bot, "_make_avatar_hash_presence", make_presence)

    assert await bot.update_vcard() is True
    assert [presence.kwargs.get("pto") for presence in bot.sent] == [
        None,
        "working@example.org/BanBot",
    ]


@pytest.mark.asyncio
async def test_concurrent_identity_updates_use_consistent_serialized_snapshots(
    monkeypatch,
    cleared_vcard_config,
):
    from envs_xmpp_core.xmpp.avatar import AvatarPayload

    import config

    bot = VCardBot()
    entered_old_load = asyncio.Event()
    release_old_load = asyncio.Event()

    monkeypatch.setattr(config, "AVATAR_PATH", "old.png", raising=False)
    monkeypatch.setattr(config, "VCARD_NICKNAME", "Old", raising=False)

    async def controlled_load(path):
        if path == "old.png":
            entered_old_load.set()
            await release_old_load.wait()
            return AvatarPayload(b"old", "image/png", "old-hash")
        assert path == "new.png"
        return AvatarPayload(b"new", "image/png", "new-hash")

    monkeypatch.setattr(bot, "_load_avatar_payload", controlled_load)

    first = asyncio.create_task(bot.update_vcard())
    await entered_old_load.wait()

    # Mutate the live config while the first publish is suspended. The first
    # generation must retain its captured profile, while the second waits for
    # the identity lock and then captures/publishes the new generation.
    config.AVATAR_PATH = "new.png"
    config.VCARD_NICKNAME = "New"
    second = asyncio.create_task(bot.update_vcard())
    await asyncio.sleep(0)

    assert bot.xep0054.published == []
    release_old_load.set()
    assert await first is True
    assert await second is True

    assert [vcard["NICKNAME"] for vcard in bot.xep0054.published] == ["Old", "New"]
    assert [vcard["PHOTO"]["BINVAL"] for vcard in bot.xep0054.published] == [
        b"old",
        b"new",
    ]
