import importlib
import json
import os
from xml.etree import ElementTree as ET

import pytest

slixmpp = pytest.importorskip("slixmpp")

from banbot.omemo import OmemoMixin, _prepare_omemo_storage_file


TEST_OMEMO_RESET_EXPECTED_RESTART_DELAY_SECONDS = 3
TEST_DEVICE_HINT_ID = 813096472
TEST_ADMINBOT_NESTED_DEVICE_HINT_ID = "9095"
OMEMO_RESET_SUCCESS_FRAGMENTS = (
    "OMEMO storage reset prepared",
    "OMEMO is disabled for this running process until restart",
    f"Restarting in {TEST_OMEMO_RESET_EXPECTED_RESTART_DELAY_SECONDS} seconds",
    "Old storage backup:",
    "Old metadata backup:",
)


def stat_mode(path):
    """Return file permission bits for test assertions."""
    return os.stat(path).st_mode & 0o777


def freeze_omemo_timestamp(monkeypatch, timestamp="20260528-123456"):
    """Patch OMEMO timestamp formatting for deterministic tests.

    This helper monkeypatches ``banbot.omemo.time.strftime`` with a stable
    timestamp while still honoring the requested format string. That keeps
    backup filename assertions deterministic without hiding format mistakes.

    Args:
        monkeypatch: Pytest monkeypatch fixture used to patch module attributes.
        timestamp: Fixed timestamp in ``YYYYMMDD-HHMMSS`` format.
    """
    omemo_module = importlib.import_module("banbot.omemo")

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
        self.omemo_reset_pending_restart = False
        self.sent = []
        self.audited = []
        self.restart_calls = []

    async def _restart_process(self):
        self.restart_calls.append("restart")

    async def bot_send_message(self, **kwargs):
        self.sent.append(kwargs)

    async def audit_event(self, event_type, **kwargs):
        self.audited.append((event_type, kwargs))


class FakeMessage:
    """Minimal message stanza test double for OMEMO helpers."""

    def __init__(self, xml, sender="sender@example.test"):
        self.xml = xml
        self._sender = sender

    def get(self, key, default=None):
        if key == "from":
            return self._sender
        return default


def make_message(xml, sender="sender@example.test"):
    """Create a fake message without reparsing OMEMO XML fixtures."""
    return FakeMessage(xml, sender=sender)


class ReadyFlag:
    """Minimal ready-state flag used by OMEMO tests."""

    def __init__(self, value=True):
        self.value = value
        self.cleared = False

    def is_set(self):
        return self.value

    def clear(self):
        self.value = False
        self.cleared = True


def write_omemo_storage(storage, payload):
    """Write OMEMO-like JSON storage for device-hint tests."""
    storage.write_text(json.dumps(payload), encoding="utf8")


def nested_device_hint_storage_payload():
    """Return OMEMO-like storage with nested device-hint structures.

    This fixture intentionally mixes nested dictionaries, lists and plain text
    so device-hint scanning can be verified without treating internal values
    such as prekey ranges or booleans as real device IDs.
    """

    return {
        "sessions": {
            "adminbot@example.org": {
                "prekeys": list(range(1, 101)),
                "enabled": True,
                "device_id": TEST_DEVICE_HINT_ID,
                # Include a nested, device-like key pattern to verify hint
                # extraction traverses nested dicts without mistaking booleans.
                "nested": {"device": True, f"dev-{TEST_ADMINBOT_NESTED_DEVICE_HINT_ID}": {}},
            },
            "moderator@example.org": {
                "session": "present",
                "notes": ["OMEMO device id 123456"],
            },
            "user2@example.org": {"device": False},
        },
        "text": "known jid moderator@example.org",
    }


def simple_device_hint_storage_payload():
    """Return minimal OMEMO-like storage with one visible device hint.

    Use this for straightforward device listing tests where the hint is present
    at a top-level device-key location. More complex traversal and negative
    matching are covered by ``nested_device_hint_storage_payload``.
    """

    return {
        "sessions": {
            "adminbot@example.org": {"device_id": TEST_DEVICE_HINT_ID},
        },
    }


@pytest.mark.omemo
def test_message_has_omemo_payload(omemo_payload_xml):
    bot = OmemoProbe()
    msg = make_message(omemo_payload_xml)
    assert bot._message_has_omemo_payload(msg) is True

    plain = make_message(ET.Element("message"))
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
    # Intentionally mixed quoting verifies parser robustness against
    # inconsistent token styles in exception messages.
    exc = RuntimeError(
        "bad recipients: "
        "frozenset({'envsbot@example.org', \"user@example.org\"})"
    )

    assert bot._extract_unusable_omemo_recipients(exc) == {
        "envsbot@example.org",
        "user@example.org",
    }


@pytest.mark.omemo
def test_extract_unusable_omemo_recipients_filters_invalid_tokens():
    bot = OmemoProbe()
    exc = RuntimeError(
        "bad recipients: "
        "frozenset({'envsbot@example.org', 'No Device', \"user@example.org\"})"
    )

    assert bot._extract_unusable_omemo_recipients(exc) == {
        "envsbot@example.org",
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
        if isinstance(recipients, set):
            bares = sorted(jid.bare for jid in recipients)
        else:
            bares = [recipients.bare]

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
    """Fake slixmpp XEP-0384 decrypt plugin."""

    def __init__(self, encrypted_namespace="eu.siacs.conversations.axolotl", result=None):
        """Initialize the fake decrypt plugin.

        Args:
            encrypted_namespace: Value returned by ``is_encrypted`` to simulate
                different encryption states in tests. Use a namespace string to
                represent an encrypted stanza, or a falsy value such as ``None``
                to represent an unencrypted stanza. The default namespace is
                the OMEMO namespace expected by XEP-0384/slixmpp.
            result: Optional decrypt result returned by ``decrypt_message``.
        """
        self.encrypted_namespace = encrypted_namespace
        self.result = result
        self.decrypt_calls = 0

    def is_encrypted(self, msg):
        """Mirror slixmpp behavior by returning the encryption namespace."""
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
    bot.omemo_ready = ReadyFlag(True)
    msg = make_message(ET.Element("message"))

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
    bot.omemo_ready = ReadyFlag(True)
    msg = make_message(omemo_payload_xml, sender="sender@example.test")

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
    bot.omemo_ready = ReadyFlag(True)
    msg = make_message(omemo_payload_xml, sender="room@example.test/dan")

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
    bot.omemo_ready = ReadyFlag(True)
    msg = make_message(omemo_payload_xml, sender="room@example.test/dan")

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
    omemo_module = importlib.import_module("banbot.omemo")
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

    freeze_omemo_timestamp(monkeypatch, "20260528-123456")

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

    freeze_omemo_timestamp(monkeypatch, "20260528-123456")

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


@pytest.mark.omemo
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


@pytest.mark.omemo
def test_collect_omemo_storage_device_hints_filters_internal_values(tmp_path):
    storage = tmp_path / "omemo.json"
    write_omemo_storage(storage, nested_device_hint_storage_payload())

    bot = OmemoProbe()
    bot.omemo_storage_file = str(storage)

    hints = bot._collect_omemo_storage_device_hints()
    assert hints["adminbot@example.org"] == {
        str(TEST_DEVICE_HINT_ID),
        TEST_ADMINBOT_NESTED_DEVICE_HINT_ID,
    }
    assert hints["moderator@example.org"] == {"123456"}
    assert hints["user2@example.org"] == set()
    assert "True" not in hints["adminbot@example.org"]
    assert "1" not in hints["adminbot@example.org"]
    assert "100" not in hints["adminbot@example.org"]


@pytest.mark.omemo
def test_format_omemo_device_ids_is_stable_and_compact():
    assert OmemoProbe._format_omemo_device_ids(set()) == (
        "storage entry found, exact device IDs not visible"
    )
    assert OmemoProbe._format_omemo_device_ids({"not-a-number"}) == (
        "storage entry found, exact device IDs not visible"
    )
    assert OmemoProbe._format_omemo_device_ids({"1", "2", "not-a-number"}) == "1, 2"
    assert OmemoProbe._format_omemo_device_ids({"10", "2", "1"}) == "1, 2, 10"

    long_ids = {str(i) for i in range(1, 20)}
    formatted = OmemoProbe._format_omemo_device_ids(long_ids, limit=3)
    assert formatted == "1, 2, 3, … (19 hints)"


@pytest.mark.omemo
@pytest.mark.asyncio
async def test_cmd_omemo_devices_lists_recipients_before_storage_hints(tmp_path, monkeypatch):
    import config

    storage = tmp_path / "omemo.json"
    write_omemo_storage(storage, simple_device_hint_storage_payload())

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
    assert f"• adminbot@example.org: {TEST_DEVICE_HINT_ID}" in body
    assert "not a guaranteed list" in body
    assert body.index("Current admin-room recipients") < body.index("Local storage hints")


@pytest.mark.omemo
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

    scheduled = []

    def mock_schedule_restart():
        scheduled.append("restart")

    monkeypatch.setattr(bot, "_schedule_omemo_reset_restart", mock_schedule_restart)

    await bot._cmd_omemo_reset("admin@conference.example.org", actor="admin@example.org", confirm=True)
    body = bot.sent[-1]["mbody"]
    for fragment in OMEMO_RESET_SUCCESS_FRAGMENTS:
        assert fragment in body
    assert bot.omemo_ready.cleared is True
    assert bot.omemo_enabled is False
    assert bot.omemo_reset_pending_restart is True
    assert bot.sent[-1]["encrypted"] is False
    assert scheduled == ["restart"]
    assert not storage.exists()
    assert metadata.exists()
    assert bot.audited[-1][0] == "omemo_reset"
    assert list(tmp_path.glob("omemo.json.bak-*"))
    assert list(tmp_path.glob("omemo.identity.json.bak-*"))


@pytest.mark.omemo
@pytest.mark.asyncio
async def test_omemo_reset_restart_helper_waits_then_restarts(monkeypatch):
    bot = OmemoProbe()
    sleeps = []

    async def fake_sleep(delay):
        sleeps.append(delay)

    monkeypatch.setattr("banbot.omemo.asyncio.sleep", fake_sleep)

    await bot._restart_after_omemo_reset()

    assert sleeps == [TEST_OMEMO_RESET_EXPECTED_RESTART_DELAY_SECONDS]
    assert bot.restart_calls == ["restart"]


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
    msg = make_message(omemo_payload_xml)

    result, encrypted = await bot._decrypt_incoming_omemo_message(msg)

    assert result is None
    assert encrypted is True


@pytest.mark.omemo
@pytest.mark.asyncio
async def test_cmd_omemo_usage_for_unknown_action():
    bot = OmemoProbe()

    await bot.cmd_omemo(["unknown"], "admin@conference.example.org")

    body = bot.sent[-1]["mbody"]
    assert "Usage:" in body
    assert "!omemo status" in body
    assert "!omemo devices" in body
    assert "!omemo reset [confirm]" in body
