from __future__ import annotations

import string
from xml.etree import ElementTree as ET

import pytest

hypothesis = pytest.importorskip("hypothesis")
from hypothesis import given, settings, strategies as st

from banbot.rtbl.utils import (
    _is_domain,
    _is_sha256,
    _looks_like_pubsub_node,
    _looks_like_pubsub_service_jid,
    _rtbl_build_payload,
    _rtbl_extract_reason,
    _rtbl_hash_jid,
)


_HEX = set("0123456789abcdef")

_DOMAIN_LABEL = st.from_regex(
    r"[a-z0-9](?:[a-z0-9-]{0,18}[a-z0-9])?",
    fullmatch=True,
)
_TLD = st.from_regex(r"[a-z]{2,8}", fullmatch=True)
_DOMAIN_HOST = st.builds(lambda label, tld: f"{label}.{tld}", _DOMAIN_LABEL, _TLD)


@settings(max_examples=100)
@given(jid=st.text(min_size=1, max_size=120))
def test_rtbl_hash_jid_is_stable_sha256_of_normalized_bare_jid(jid):
    digest = _rtbl_hash_jid(jid)

    assert len(digest) == 64
    assert set(digest) <= _HEX
    assert digest == _rtbl_hash_jid(jid.strip().lower())


@settings(max_examples=100)
@given(value=st.text(alphabet=string.hexdigits, min_size=64, max_size=64))
def test_is_sha256_accepts_only_lowercase_hex_digest(value):
    normalized = value.lower()

    assert _is_sha256(normalized) is True
    assert _is_sha256(normalized.upper()) is (normalized.upper() == normalized)


@settings(max_examples=100)
@given(
    value=st.text(min_size=0, max_size=80).filter(
        lambda s: len(s) != 64 or any(ch not in string.hexdigits for ch in s)
    )
)
def test_is_sha256_rejects_wrong_size_or_non_hex_values(value):
    assert _is_sha256(value) is False


@settings(max_examples=100)
@given(label=_DOMAIN_LABEL, tld=_TLD)
def test_is_domain_accepts_plain_and_wildcard_domains(label, tld):
    domain = f"{label}.{tld}"

    assert _is_domain(domain) is True
    assert _is_domain(f"*.{domain}") is True


@settings(max_examples=100)
@given(value=st.text(min_size=0, max_size=80).filter(lambda s: "@" in s or "." not in s))
def test_is_domain_rejects_jids_and_values_without_dot(value):
    assert _is_domain(value) is False


@settings(max_examples=100)
@given(host=_DOMAIN_HOST)
def test_looks_like_pubsub_service_jid_accepts_domains_and_component_jids(host):
    assert _looks_like_pubsub_service_jid(host) is True
    assert _looks_like_pubsub_service_jid(f"pubsub.{host}") is True
    assert _looks_like_pubsub_service_jid(f"service@{host}") is True


@pytest.mark.parametrize(
    "service",
    [
        "",
        "   ",
        "pubsub",
        ".example.org",
        "example.org.",
        "pubsub.example.1",
        "pub sub.example.org",
        "pubsub.example.org/resource",
        "0-.aa",
        "-bad.example.org",
        "bad-.example.org",
    ],
)
def test_looks_like_pubsub_service_jid_rejects_invalid_services(service):
    assert _looks_like_pubsub_service_jid(service) is False


@settings(max_examples=100)
@given(node=st.text(min_size=0, max_size=40).filter(lambda value: not value.strip()))
def test_looks_like_pubsub_node_rejects_empty_or_blank_values(node):
    assert _looks_like_pubsub_node(node) is False


@pytest.mark.parametrize(
    "node",
    [
        "foo bar",
        "foo\tbar",
        "foo\nbar",
        "muc_bans_sha256 other",
    ],
)
def test_looks_like_pubsub_node_rejects_internal_whitespace(node):
    assert _looks_like_pubsub_node(node) is False


@pytest.mark.parametrize(
    "node",
    [
        "muc_bans_sha256",
        " muc_bans_sha256 ",
        "\tmuc_bans_sha256\t",
        "0 ",
    ],
)
def test_looks_like_pubsub_node_allows_surrounding_whitespace(node):
    assert _looks_like_pubsub_node(node) is True


@settings(max_examples=100)
@given(
    node=st.text(min_size=1, max_size=40).filter(
        lambda value: value == value.strip()
        and value
        and not any(ch.isspace() for ch in value)
    )
)
def test_looks_like_pubsub_node_accepts_non_blank_values_without_internal_whitespace(node):
    assert _looks_like_pubsub_node(node) is True


@settings(max_examples=100)
@given(reason=st.text(min_size=1, max_size=80).filter(lambda value: bool(value.strip())))
def test_rtbl_build_payload_roundtrips_reason_text(reason):
    payload = _rtbl_build_payload(reason)

    assert _rtbl_extract_reason(payload) == reason.strip()


@pytest.mark.parametrize("reason", [" ", "\t", "\n", "   "])
def test_rtbl_build_payload_strips_blank_reason_text(reason):
    payload = _rtbl_build_payload(reason)

    assert _rtbl_extract_reason(payload) == ""


def test_rtbl_build_payload_without_comment_has_no_reason():
    payload = _rtbl_build_payload(None)

    assert _rtbl_extract_reason(payload) is None


@pytest.mark.parametrize(
    "xml, expected",
    [
        ("<report xmlns='urn:xmpp:reporting:1'><text>spam</text></report>", "spam"),
        ("<item><text>abuse</text></item>", "abuse"),
        ("<item><other>ignored</other></item>", None),
    ],
)
def test_rtbl_extract_reason_supports_reporting_and_generic_text(xml, expected):
    assert _rtbl_extract_reason(ET.fromstring(xml)) == expected
