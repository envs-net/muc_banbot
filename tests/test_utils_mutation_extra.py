"""Focused edge tests for utility helpers targeted by mutation testing."""

import pytest

from banbot.utils import (
    bare_jid,
    domain_matches,
    human_time,
    looks_like_domain,
    normalize_ban_target,
    paginate_lines,
    parse_duration,
    resolve_page,
    safe_jid,
    validate_domain_ban,
    validate_jid_format,
)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (1, "1s"),
        (61, "1m 1s"),
        (3600, "1h"),
        (3601, "1h 1s"),
        (86400, "1d"),
        (172800 + 7200 + 120 + 3, "2d 2h 2m 3s"),
    ],
)
def test_human_time_omits_zero_units_but_keeps_order(value, expected):
    assert human_time(value) == expected


@pytest.mark.parametrize(
    "value",
    ["1", "1w", "1Mins", "+m", "  ", "1.5h", "1/2h", "--1m"],
)
def test_parse_duration_rejects_non_integer_or_unknown_suffixes(value):
    with pytest.raises(ValueError):
        parse_duration(value)


def test_safe_jid_replaces_all_at_signs_without_changing_other_text():
    assert safe_jid("a@b@c") == "a@\u200bb@\u200bc"
    assert safe_jid("plain") == "plain"


@pytest.mark.parametrize(
    ("jid", "expected"),
    [
        ("user@example.org/resource/extra", "user@example.org"),
        ("USER@EXAMPLE.ORG", "user@example.org"),
        ("", None),
    ],
)
def test_bare_jid_resource_and_empty_handling(jid, expected):
    assert bare_jid(jid) == expected


@pytest.mark.parametrize(
    "jid",
    ["user@@example.org", "user@", "@example.org", "user@example", "userexample.org"],
)
def test_validate_jid_format_rejects_malformed_values(jid):
    assert validate_jid_format(jid) is False


@pytest.mark.parametrize(
    "jid",
    ["user@example.org", "u@sub.example.org", "user-name@example.co.uk"],
)
def test_validate_jid_format_accepts_basic_bare_jids(jid):
    assert validate_jid_format(jid) is True


@pytest.mark.parametrize(
    "domain",
    ["*.example.org", "example.org", "*.sub.example.org", "Example.ORG.", "..example.org.."],
)
def test_validate_domain_ban_accepts_precise_domains_after_normalization(domain):
    assert validate_domain_ban(domain)[0] is True


@pytest.mark.parametrize("domain", ["", ".", "org", "*.org", "*.", "localhost"])
def test_validate_domain_ban_rejects_generic_or_empty_domains(domain):
    ok, msg = validate_domain_ban(domain)
    assert ok is False
    assert "too generic" in msg


def test_domain_matches_is_boundary_aware_and_normalizes_case_and_dots():
    assert domain_matches("Sub.Example.Org.", ".example.org.") is True
    assert domain_matches("example.org", "example.org") is True
    assert domain_matches("badexample.org", "example.org") is False
    assert domain_matches("example.org.evil", "example.org") is False
    assert domain_matches("", "example.org") is False


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("example.org ", True),
        ("sub.example.org", True),
        ("*.example.org", False),
        ("user@example.org", False),
        ("example.org/resource", False),
        ("nick", False),
        (None, False),
    ],
)
def test_looks_like_domain_boundaries(value, expected):
    assert looks_like_domain(value) is expected


def test_normalize_ban_target_prefers_jid_over_nick_and_preserves_domain_marker():
    assert normalize_ban_target(jid="User@Example.Org/Res", nick="Nick") == (
        "jid",
        "user@example.org",
        "user@example.org",
        "nick",
    )
    assert normalize_ban_target(jid="*.Example.Org.", nick="Nick") == (
        "domain",
        "example.org",
        "*.example.org.",
        "nick",
    )


@pytest.mark.parametrize("nick", [" Nick ", "Ädmin", "MIXED Case"])
def test_normalize_ban_target_nick_strips_and_lowercases(nick):
    target_type, target, normalized_jid, normalized_nick = normalize_ban_target(nick=nick)
    assert target_type == "nick"
    assert target == nick.lower().strip()
    assert normalized_jid is None
    assert normalized_nick == target


def test_paginate_lines_and_resolve_page_edge_cases():
    assert paginate_lines([], 99, per_page=10) == ([], 1, 1, 0)
    assert paginate_lines(["a", "b", "c"], -5, per_page=2) == (["a", "b"], 1, 2, 3)
    assert paginate_lines(["a", "b", "c"], 99, per_page=2) == (["c"], 2, 2, 3)
    assert resolve_page(-1, 0, per_page=10) == 1
    assert resolve_page(-1, 21, per_page=10) == 3
