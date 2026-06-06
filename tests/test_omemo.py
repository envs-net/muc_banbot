import json
import os
from xml.etree import ElementTree as ET

import pytest

slixmpp = pytest.importorskip("slixmpp")

from banbot.omemo import OmemoMixin, _prepare_omemo_storage_file


def stat_mode(path):
    return os.stat(path).st_mode & 0o777


def freeze_omemo_timestamp(monkeypatch, timestamp="20260528-123456"):
    """Patch banbot.omemo timestamp formatting while still honoring fmt."""
    import banbot.omemo as omemo_module

    original_strftime = omemo_module.time.strftime
    parsed = omemo_module.time.strptime(timestamp, "%Y%m%d-%H%M%S")

    def fake_strftime(fmt):
        return original_strftime(fmt, parsed)

    monkeypatch.setattr("banbot.omemo.time.strftime", fake_strftime)


@pytest.mark.omemo
def test_prepare_omemo_storage_file_creates_private_path(tmp_path):
    storage = tmp_path / "private" / "omemo.json"
    result = _prepare_omemo_storage_file(str(storage))

    assert result == storage
    assert storage.exists()
    assert stat_mode(storage) == 0o600
    assert stat_mode(storage.parent) == 0o700
    assert storage.read_text(encoding="utf8").strip() == "{}"


@pytest.mark.omemo
def test_prepare_omemo_storage_file_updates_existing_file_permissions(tmp_path):
    storage = tmp_path / "private" / "omemo.json"
    storage.parent.mkdir(mode=0o777, parents=True, exist_ok=True)
    storage.write_text('{"existing": true}', encoding="utf8")
    os.chmod(storage.parent, 0o777)
    os.chmod(storage, 0o666)

    result = _prepare_omemo_storage_file(str(storage))

    assert result == storage
    assert storage.exists()
    assert stat_mode(storage) == 0o600
    assert stat_mode(storage.parent) == 0o700
    assert storage.read_text(encoding="utf8").strip() == '{"existing": true}'


@pytest.mark.omemo
def test_prepare_omemo_storage_file_rejects_directory(tmp_path):
    with pytest.raises(RuntimeError):
        _prepare_omemo_storage_file(str(tmp_path))


class OmemoProbe(OmemoMixin):
    def __init__(self):
        self.omemo_enabled = True
        self.plugin = {}
        self.omemo_ready = None
        self.occupants = {
            "room@conference.example.test": {
                "alice": {"jid": "alice@example.test/mobile"},
                "bob": {"jid": "bob@example.test/desktop"},
                "anon": {},
                "self": {"jid": "bot@example.test/service"},
            }
        }
        self.boundjid = slixmpp.JID("bot@example.test/service")
        self.command_prefix = "!"
        self.omemo_storage_file = "data/omemo.json"
        self.omemo_auto_encrypt_admin_room = True
        self.omemo_plaintext_fallback = False
        self.omemo_reset_on_identity_change = True
        self.sent = []
        self.audited = []

    async def bot_send_message(self, **kwargs):
        self.sent.append(kwargs)

    async def audit_event(self, event_type, **kwargs):
        self.audited.append((event_type, kwargs))


@pytest.mark.omemo
def test_message_has_omemo_payload(omemo_payload_xml):
    bot = OmemoProbe()
    msg = type("Msg", (), {"xml": omemo_payload_xml})()
    assert bot._message_has_omemo_payload(msg) is True

    plain = type("Msg", (), {"xml": ET.Element("message")})()
    assert bot._message_has_omemo_payload(plain) is False


@pytest.mark.omemo
@pytest.mark.asyncio
async def test_omemo_recipients_for_room_uses_visible_occupant_jids_only():
    bot = OmemoProbe()
    recipients = await bot._omemo_recipients_for_room("room@conference.example.test")
    bares = {jid.bare for jid in recipients}

    assert "alice@example.test" in bares
    assert "bob@example.test" in bares
    assert "bot@example.test" in bares  # own devices are included when real recipients exist
    assert "anon" not in bares


@pytest.mark.omemo
def test_extract_unusable_omemo_recipients():
    bot = OmemoProbe()
    exc = RuntimeError("bad recipients: frozenset({'envsbot@envs.net', \"user@example.org\"})")

    assert bot._extract_unusable_omemo_recipients(exc) == {
        "envsbot@envs.net",
        "user@example.org",
    }


@pytest.mark.omemo
def test_extract_unusable_omemo_recipients_filters_invalid_tokens():
    bot = OmemoProbe()
    exc = RuntimeError("bad recipients: frozenset({'envsbot@envs.net', 'No Device', \"user@example.org\"})")

    assert bot._extract_unusable_omemo_recipients(exc) == {
        "envsbot@envs.net",
        "user@example.org",
    }


class FakeEncryptedMessage:
    def __init__(self, label="encrypted"):
        self.label = label
        self.sent = False

    def send(self):
        self.sent = True


class FakeEncryptPlugin:
    def __init__(self):
        self.calls = []

    async def encrypt_message(self, msg, recipients):
        bares = sorted(jid.bare for jid in recipients) if isinstance(recipients, set) else [recipients.bare]
        self.calls.append(bares)
        if "bad@example.test" in bares:
            raise RuntimeError("bad recipients: frozenset({'bad@example.test'})")
        return FakeEncryptedMessage("ok"), None


@pytest.mark.omemo
@pytest.mark.asyncio
async def test_encrypt_and_send_omemo_retries_without_unusable_recipients():
    bot = OmemoProbe()
    plugin = FakeEncryptPlugin()
    bot.plugin = {"xep_0384": plugin}

    result = await bot._encrypt_and_send_omemo_message(
        object(),
        {slixmpp.JID("good@example.test"), slixmpp.JID("bad@example.test")},
        mto="room@conference.example.test",
    )

    assert result.sent is True
    assert plugin.calls == [
        ["bad@example.test", "good@example.test"],
        ["good@example.test"],
    ]


@pytest.mark.omemo
@pytest.mark.asyncio
async def test_encrypt_and_send_omemo_does_not_plaintext_fallback_when_all_recipients_unusable():
    bot = OmemoProbe()
    plugin = FakeEncryptPlugin()
    bot.plugin = {"xep_0384": plugin}

    with pytest.raises(RuntimeError, match="No usable OMEMO recipients"):
        await bot._encrypt_and_send_omemo_message(
            object(),
            {slixmpp.JID("bad@example.test")},
            mto="room@conference.example.test",
        )


class FakeDecryptPlugin:
    def __init__(self, encrypted_namespace="eu.siacs.conversations.axolotl", result=None):
        self.encrypted_namespace = encrypted_namespace
        self.result = result
        self.decrypt_calls = 0

    def is_encrypted(self, msg):
        return self.encrypted_namespace

    async def decrypt_message(self, msg):
        self.decrypt_calls += 1
        return self.result or msg


@pytest.mark.omemo
@pytest.mark.asyncio
async def test_decrypt_incoming_plaintext_message_does_not_call_omemo_backend():
    bot = OmemoProbe()
    plugin = FakeDecryptPlugin()
    bot.plugin = {"xep_0384": plugin}
    bot.omemo_ready = type("Ready", (), {"is_set": lambda self: True})()
    msg = type("Msg", (), {"xml": ET.Element("message")})()

    result, encrypted = await bot._decrypt_incoming_omemo_message(msg)

    assert result is msg
    assert encrypted is False
    assert plugin.decrypt_calls == 0


@pytest.mark.omemo
@pytest.mark.asyncio
async def test_decrypt_incoming_omemo_message_returns_decrypted_message(omemo_payload_xml):
    bot = OmemoProbe()
    decrypted = object()
    plugin = FakeDecryptPlugin(result=(decrypted, object()))
    bot.plugin = {"xep_0384": plugin}
    bot.omemo_ready = type("Ready", (), {"is_set": lambda self: True})()
    msg = type("Msg", (), {"xml": omemo_payload_xml, "get": lambda self, key: "sender@example.test"})()

    result, encrypted = await bot._decrypt_incoming_omemo_message(msg)

    assert result is decrypted
    assert encrypted is True
    assert plugin.decrypt_calls == 1

class FailingDecryptPlugin(FakeDecryptPlugin):
    def __init__(self, exc):
        super().__init__()
        self.exc = exc

    async def decrypt_message(self, msg):
        self.decrypt_calls += 1
        raise self.exc


@pytest.mark.omemo
@pytest.mark.asyncio
async def test_decrypt_incoming_omemo_device_info_failures_are_logged_as_info(
    omemo_payload_xml,
    caplog,
):
    bot = OmemoProbe()
    plugin = FailingDecryptPlugin(
        RuntimeError(
            "Couldn't find public information about the device which sent this message. "
            "I.e. the device either does not appear in the device list of the sending "
            "XMPP account, or the bundle of the sending device could not be downloaded."
        )
    )
    bot.plugin = {"xep_0384": plugin}
    bot.omemo_ready = type("Ready", (), {"is_set": lambda self: True})()
    msg = type(
        "Msg",
        (),
        {"xml": omemo_payload_xml, "get": lambda self, key: "room@example.test/dan"},
    )()

    with caplog.at_level("INFO", logger="banbot.omemo"):
        result, encrypted = await bot._decrypt_incoming_omemo_message(msg)

    assert result is None
    assert encrypted is True
    assert plugin.decrypt_calls == 1
    assert "sender device information is unavailable" in caplog.text
    assert "failed to decrypt incoming message" not in caplog.text


@pytest.mark.omemo
@pytest.mark.asyncio
async def test_decrypt_incoming_omemo_unexpected_failures_log_sanitized_warning(
    omemo_payload_xml,
    caplog,
):
    bot = OmemoProbe()
    plugin = FailingDecryptPlugin(RuntimeError("unexpected decrypt failure"))
    bot.plugin = {"xep_0384": plugin}
    bot.omemo_ready = type("Ready", (), {"is_set": lambda self: True})()
    msg = type(
        "Msg",
        (),
        {"xml": omemo_payload_xml, "get": lambda self, key: "room@example.test/dan"},
    )()

    with caplog.at_level("WARNING", logger="banbot.omemo"):
        result, encrypted = await bot._decrypt_incoming_omemo_message(msg)

    assert result is None
    assert encrypted is True
    assert "failed to decrypt incoming message" in caplog.text

    # Do not log raw decrypt exception details. They may contain sensitive
    # OMEMO/account/device metadata and are flagged by CodeQL.
    assert "unexpected decrypt failure" not in caplog.text
    assert "room@example.test/dan" not in caplog.text


@pytest.mark.omemo
def test_configure_omemo_missing_optional_dependencies_disables_feature(monkeypatch, caplog):
    import banbot.omemo as omemo_module
    import config

    bot = OmemoProbe()
    bot.registered = []
    bot.handlers = []

    def register_plugin(*args, **kwargs):
        bot.registered.append((args, kwargs))

    def add_event_handler(*args, **kwargs):
        bot.handlers.append((args, kwargs))

    bot.register_plugin = register_plugin
    bot.add_event_handler = add_event_handler

    monkeypatch.setattr(config, "OMEMO_ENABLED", True, raising=False)
    monkeypatch.setattr(config, "OMEMO_STORAGE_FILE", "data/omemo.json", raising=False)
    monkeypatch.setattr(config, "OMEMO_AUTO_ENCRYPT_ADMIN_ROOM", False, raising=False)
    monkeypatch.setattr(config, "OMEMO_PLAINTEXT_FALLBACK", False, raising=False)
    monkeypatch.setattr(omemo_module, "OMEMO_AVAILABLE", False)
    monkeypatch.setattr(omemo_module, "XEP_0384Impl", None)

    with caplog.at_level("WARNING", logger="banbot.omemo"):
        bot.configure_omemo()

    assert bot.omemo_enabled is False
    assert bot.registered == []
    assert bot.handlers == []
    assert "optional dependencies are missing" in caplog.text
    assert "requirements-omemo.txt" in caplog.text


@pytest.mark.omemo
def test_omemo_identity_metadata_path_matches_storage_name(tmp_path):
    from banbot.omemo import _omemo_identity_metadata_path

    assert _omemo_identity_metadata_path(tmp_path / "omemo.json").name == "omemo.identity.json"
    assert _omemo_identity_metadata_path(tmp_path / "omemo-store").name == "omemo-store.identity.json"


@pytest.mark.omemo
def test_ensure_omemo_identity_metadata_writes_first_identity(tmp_path):
    from banbot.omemo import (
        _ensure_omemo_identity_metadata,
        _omemo_identity_metadata_path,
        _read_omemo_identity_metadata,
    )

    storage = tmp_path / "data" / "omemo.json"
    identity = {"jid": "adminbot@example.org", "resource": "bot", "nick": "AdminBot"}

    backup = _ensure_omemo_identity_metadata(
        storage,
        identity,
        reset_on_change=True,
    )

    metadata = _omemo_identity_metadata_path(storage)
    assert backup is None
    assert _read_omemo_identity_metadata(metadata) == identity
    assert stat_mode(metadata) == 0o600
    assert stat_mode(metadata.parent) == 0o700


@pytest.mark.omemo
def test_ensure_omemo_identity_metadata_rotates_existing_store_without_metadata(tmp_path, monkeypatch):
    from banbot.omemo import (
        _ensure_omemo_identity_metadata,
        _omemo_identity_metadata_path,
        _read_omemo_identity_metadata,
    )

    storage = tmp_path / "data" / "omemo.json"
    storage.parent.mkdir()
    storage.write_text('{"sessions": {"old": true}}\n', encoding="utf8")
    os.chmod(storage, 0o600)
    identity = {"jid": "adminbot@example.org", "resource": "bot", "nick": "AdminBot"}

    freeze_omemo_timestamp(monkeypatch)

    backup = _ensure_omemo_identity_metadata(
        storage,
        identity,
        reset_on_change=True,
    )

    assert backup == tmp_path / "data" / "omemo.json.bak-20260528-123456"
    assert backup.exists()
    assert not storage.exists()
    assert _read_omemo_identity_metadata(_omemo_identity_metadata_path(storage)) == identity


@pytest.mark.omemo
def test_ensure_omemo_identity_metadata_rotates_storage_on_identity_change(tmp_path, monkeypatch):
    from banbot.omemo import (
        _ensure_omemo_identity_metadata,
        _omemo_identity_metadata_path,
        _read_omemo_identity_metadata,
        _write_omemo_identity_metadata,
    )

    storage = tmp_path / "data" / "omemo.json"
    storage.parent.mkdir()
    storage.write_text('{"old": true}\n', encoding="utf8")
    os.chmod(storage, 0o600)

    metadata = _omemo_identity_metadata_path(storage)
    old_identity = {"jid": "adminbot@example.org", "resource": "old", "nick": "adminbot"}
    new_identity = {"jid": "adminbot@example.org", "resource": "new", "nick": "AdminBot"}
    _write_omemo_identity_metadata(metadata, old_identity)

    freeze_omemo_timestamp(monkeypatch)

    backup = _ensure_omemo_identity_metadata(
        storage,
        new_identity,
        reset_on_change=True,
    )

    assert backup == tmp_path / "data" / "omemo.json.bak-20260528-123456"
    assert backup.exists()
    assert backup.read_text(encoding="utf8") == '{"old": true}\n'
    assert not storage.exists()
    assert (tmp_path / "data" / "omemo.identity.json.bak-20260528-123456").exists()
    assert _read_omemo_identity_metadata(metadata) == new_identity


@pytest.mark.omemo
def test_ensure_omemo_identity_metadata_keeps_storage_when_reset_disabled(tmp_path):
    from banbot.omemo import (
        _ensure_omemo_identity_metadata,
        _omemo_identity_metadata_path,
        _read_omemo_identity_metadata,
        _write_omemo_identity_metadata,
    )

    storage = tmp_path / "data" / "omemo.json"
    storage.parent.mkdir()
    storage.write_text('{"old": true}\n', encoding="utf8")
    os.chmod(storage, 0o600)

    metadata = _omemo_identity_metadata_path(storage)
    old_identity = {"jid": "adminbot@example.org", "resource": "old", "nick": "adminbot"}
    new_identity = {"jid": "adminbot@example.org", "resource": "new", "nick": "AdminBot"}
    _write_omemo_identity_metadata(metadata, old_identity)

    backup = _ensure_omemo_identity_metadata(
        storage,
        new_identity,
        reset_on_change=False,
    )

    assert backup is None
    assert storage.exists()
    assert storage.read_text(encoding="utf8") == '{"old": true}\n'
    assert _read_omemo_identity_metadata(metadata) == old_identity


class ReadyFlag:
    def __init__(self, value=True):
        self.value = value
        self.cleared = False

    def is_set(self):
        return self.value

    def clear(self):
        self.value = False
        self.cleared = True


def test_omemo_storage_status_reports_missing_file_and_identity(monkeypatch, tmp_path):
    import config
    from banbot.omemo import _write_omemo_identity_metadata, _omemo_identity_metadata_path

    storage = tmp_path / "omemo.json"
    bot = OmemoProbe()
    bot.omemo_storage_file = str(storage)
    bot.omemo_ready = ReadyFlag(True)

    monkeypatch.setattr(config, "JID", "bot@example.test", raising=False)
    monkeypatch.setattr(config, "RESOURCE", "service", raising=False)
    monkeypatch.setattr(config, "NICK", "BanBot", raising=False)

    lines = bot._omemo_storage_status_lines()
    body = "\n".join(lines)
    assert "OMEMO enabled: True" in body
    assert "OMEMO ready: True" in body
    assert "Storage file does not exist yet" in body
    assert "No identity metadata exists yet" in body

    storage.write_text("{}", encoding="utf8")
    _write_omemo_identity_metadata(
        _omemo_identity_metadata_path(storage),
        {"jid": "bot@example.test", "resource": "service", "nick": "BanBot"},
    )
    body = "\n".join(bot._omemo_storage_status_lines())
    assert "Storage size:" in body
    assert "Storage permissions: 0o" in body
    assert "Identity matches: True" in body

    _write_omemo_identity_metadata(
        _omemo_identity_metadata_path(storage),
        {"jid": "other@example.test", "resource": "service", "nick": "BanBot"},
    )
    body = "\n".join(bot._omemo_storage_status_lines())
    assert "Identity matches: False" in body


def test_collect_omemo_storage_device_hints_filters_internal_values(tmp_path):
    storage = tmp_path / "omemo.json"
    storage.write_text(
        json.dumps(
            {
                "sessions": {
                    "adminbot@envs.net": {
                        "prekeys": list(range(1, 101)),
                        "enabled": True,
                        "device_id": 813096472,
                        "nested": {"device": True, "dev-9095": {}},
                    },
                    "creme@envs.net": {"session": "present", "notes": ["OMEMO device id 123456"]},
                    "dan@envs.net": {"device": False},
                },
                "text": "known jid creme@envs.net",
            }
        ),
        encoding="utf8",
    )

    bot = OmemoProbe()
    bot.omemo_storage_file = str(storage)

    hints = bot._collect_omemo_storage_device_hints()
    assert hints["adminbot@envs.net"] == {"813096472", "9095"}
    assert hints["creme@envs.net"] == {"123456"}
    assert hints["dan@envs.net"] == set()
    assert "True" not in hints["adminbot@envs.net"]
    assert "1" not in hints["adminbot@envs.net"]
    assert "100" not in hints["adminbot@envs.net"]


def test_format_omemo_device_ids_is_stable_and_compact():
    assert OmemoProbe._format_omemo_device_ids(set()) == "storage entry found, exact device IDs not visible"
    assert OmemoProbe._format_omemo_device_ids({"not-a-number"}) == "storage entry found, exact device IDs not visible"
    assert OmemoProbe._format_omemo_device_ids({"10", "2", "1"}) == "1, 2, 10"

    long_ids = {str(i) for i in range(1, 20)}
    formatted = OmemoProbe._format_omemo_device_ids(long_ids, limit=3)
    assert formatted == "1, 2, 3, … (19 hints)"


@pytest.mark.asyncio
async def test_cmd_omemo_devices_lists_recipients_before_storage_hints(tmp_path, monkeypatch):
    import config

    storage = tmp_path / "omemo.json"
    storage.write_text(json.dumps({"adminbot@envs.net": {"device_id": 813096472}}), encoding="utf8")

    bot = OmemoProbe()
    bot.omemo_storage_file = str(storage)
    bot.omemo_enabled = True
    monkeypatch.setattr(config, "ADMIN_ROOM", "room@conference.example.test", raising=False)

    await bot._cmd_omemo_devices("admin@conference.example.org")
    body = bot.sent[-1]["mbody"]

    assert body.startswith("🔐 OMEMO Devices")
    assert "Current admin-room recipients: 3" in body
    assert "• alice@example.test" in body
    assert "• bob@example.test" in body
    assert "Local storage hints:" in body
    assert "• adminbot@envs.net: 813096472" in body
    assert "not a guaranteed list" in body
    assert body.index("Current admin-room recipients") < body.index("Local storage hints")


@pytest.mark.asyncio
async def test_cmd_omemo_reset_requires_confirm_and_rotates_storage(tmp_path, monkeypatch):
    import config

    storage = tmp_path / "omemo.json"
    storage.write_text('{"old": true}', encoding="utf8")
    metadata = storage.with_name("omemo.identity.json")
    metadata.write_text('{"jid": "old@example.test"}', encoding="utf8")

    bot = OmemoProbe()
    bot.omemo_storage_file = str(storage)
    bot.omemo_ready = ReadyFlag(True)
    monkeypatch.setattr(config, "JID", "bot@example.test", raising=False)
    monkeypatch.setattr(config, "RESOURCE", "service", raising=False)
    monkeypatch.setattr(config, "NICK", "BanBot", raising=False)

    await bot._cmd_omemo_reset("admin@conference.example.org", actor="admin@example.org", confirm=False)
    assert storage.exists()
    assert "Confirm with: !omemo reset confirm" in bot.sent[-1]["mbody"]
    assert bot.sent[-1]["encrypted"] is False

    await bot._cmd_omemo_reset("admin@conference.example.org", actor="admin@example.org", confirm=True)
    body = bot.sent[-1]["mbody"]
    assert "OMEMO storage reset prepared" in body
    assert "OMEMO is disabled for this running process until restart" in body
    assert "Restart the bot now" in body
    assert "Old storage backup:" in body
    assert "Old metadata backup:" in body
    assert bot.omemo_ready.cleared is True
    assert bot.omemo_enabled is False
    assert bot.omemo_reset_pending_restart is True
    assert bot.sent[-1]["encrypted"] is False
    assert not storage.exists()
    assert metadata.exists()
    assert bot.audited[-1][0] == "omemo_reset"
    assert list(tmp_path.glob("omemo.json.bak-*"))
    assert list(tmp_path.glob("omemo.identity.json.bak-*"))


@pytest.mark.omemo
def test_should_not_encrypt_while_omemo_reset_is_pending_restart():
    bot = OmemoProbe()
    bot.omemo_enabled = True
    bot.omemo_reset_pending_restart = True

    assert bot._should_encrypt_message(
        mto="admin@conference.example.org",
        mtype="groupchat",
        encrypted=True,
    ) is False


@pytest.mark.omemo
@pytest.mark.asyncio
async def test_decrypt_encrypted_message_after_reset_pending_restart_is_rejected(omemo_payload_xml):
    bot = OmemoProbe()
    bot.omemo_enabled = True
    bot.omemo_reset_pending_restart = True
    msg = type("Msg", (), {"xml": omemo_payload_xml})()

    result, encrypted = await bot._decrypt_incoming_omemo_message(msg)

    assert result is None
    assert encrypted is True


@pytest.mark.asyncio
async def test_cmd_omemo_usage_for_unknown_action():
    bot = OmemoProbe()

    await bot.cmd_omemo(["unknown"], "admin@conference.example.org")

    body = bot.sent[-1]["mbody"]
    assert "Usage:" in body
    assert "!omemo status" in body
    assert "!omemo devices" in body
    assert "!omemo reset [confirm]" in body
