from xml.etree import ElementTree as ET

from banbot.rtbl_utils import (
    _is_domain,
    _is_sha256,
    _looks_like_pubsub_node,
    _looks_like_pubsub_service_jid,
    _rtbl_build_payload,
    _rtbl_extract_reason,
    _rtbl_hash_jid,
)


def test_rtbl_hash_jid_normalizes_bare_jid():
    assert _rtbl_hash_jid("User@Example.org") == _rtbl_hash_jid("user@example.org")
    assert len(_rtbl_hash_jid("user@example.org")) == 64


def test_rtbl_item_classification():
    assert _is_sha256("a" * 64)
    assert not _is_sha256("g" * 64)
    assert _is_domain("example.org")
    assert _is_domain("*.example.org")
    assert not _is_domain("user@example.org")


def test_pubsub_service_and_node_validation():
    assert _looks_like_pubsub_service_jid("pubsub.example.org")
    assert _looks_like_pubsub_service_jid("service@example.org")
    assert not _looks_like_pubsub_service_jid("localhost")
    assert not _looks_like_pubsub_service_jid("bad service.example.org")
    assert _looks_like_pubsub_node("muc_bans_sha256")
    assert not _looks_like_pubsub_node("bad node")


def test_rtbl_payload_reason_roundtrip():
    payload = _rtbl_build_payload("spam wave")
    assert _rtbl_extract_reason(payload) == "spam wave"
    assert _rtbl_extract_reason(None) is None
    plain = ET.Element("wrapper")
    text = ET.SubElement(plain, "text")
    text.text = "fallback"
    assert _rtbl_extract_reason(plain) == "fallback"
