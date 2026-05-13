"""Shared pytest fixtures and fake XMPP objects for muc_banbot tests."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace
from xml.etree import ElementTree as ET

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class FakeFrom:
    def __init__(self, bare: str, full: str | None = None) -> None:
        self.bare = bare
        self.full = full or bare

    def __str__(self) -> str:
        return self.full


class FakeIncomingMessage:
    """Small mapping-like object that mimics the fields used by CommandMixin."""

    def __init__(
        self,
        *,
        room: str = "room@conference.example.test",
        nick: str = "Alice",
        body: str = "!help",
        full_from: str | None = None,
        xml: ET.Element | None = None,
    ) -> None:
        self._data = {
            "from": FakeFrom(room, full_from or f"{room}/{nick}"),
            "mucnick": nick,
            "body": body,
        }
        self.xml = xml or ET.Element("message")

    def __getitem__(self, key: str):
        return self._data[key]

    def __setitem__(self, key: str, value) -> None:
        self._data[key] = value

    def get(self, key: str, default=None):
        return self._data.get(key, default)


class FakeOutgoingMessage:
    """Minimal Slixmpp Message stand-in used by messaging/OMEMO tests."""

    def __init__(self, *, mto: str, mbody: str, mtype: str = "groupchat") -> None:
        self.fields = {"to": mto, "body": mbody, "type": mtype}
        self.sent = False

    def __getitem__(self, key: str):
        if key == "html":
            return self.fields.setdefault("html", {})
        return self.fields.get(key)

    def __setitem__(self, key: str, value) -> None:
        self.fields[key] = value

    def send(self) -> None:
        self.sent = True


@pytest.fixture
def fake_msg_factory():
    return FakeIncomingMessage


@pytest.fixture
def omemo_payload_xml():
    message = ET.Element("message")
    encrypted = ET.SubElement(message, "{eu.siacs.conversations.axolotl}encrypted")
    ET.SubElement(encrypted, "header", {"sid": "123"})
    ET.SubElement(encrypted, "payload")
    return message


@pytest.fixture
def temp_db_path(tmp_path, monkeypatch):
    db_path = tmp_path / "banbot-test.sqlite3"
    import config
    import banbot.db as db_module

    monkeypatch.setattr(config, "DB_FILE", str(db_path), raising=False)
    monkeypatch.setattr(db_module, "DB_FILE", str(db_path), raising=False)
    return db_path


@pytest.fixture
def disable_integration_guard():
    if os.environ.get("RUN_XMPP_INTEGRATION") != "1":
        pytest.skip("set RUN_XMPP_INTEGRATION=1 to run live XMPP integration tests")
