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
    wants_all_pages,
    without_all_pages_arg,
)


@pytest.mark.parametrize(
    ("value", "expected"),
    [("10s", 10), ("5m", 300), ("2h", 7200), ("1d", 86400), ("3D", 259200)],
)
def test_parse_duration_valid(value, expected):
    assert parse_duration(value) == expected


@pytest.mark.parametrize("value", ["", "m", "0m", "-1m", "10x", "abc"])
def test_parse_duration_invalid(value):
    with pytest.raises(ValueError):
        parse_duration(value)


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [(0, "permanent"), (-5, "permanent"), (59, "59s"), (60, "1m"), (3661, "1h 1m 1s"), (90061, "1d 1h 1m 1s")],
)
def test_human_time(seconds, expected):
    assert human_time(seconds) == expected


def test_bare_and_safe_jid():
    assert bare_jid("User@Example.org/Resource") == "user@example.org"
    assert bare_jid(None) is None
    assert safe_jid("user@example.org") == "user@\u200bexample.org"


@pytest.mark.parametrize(
    ("jid", "expected"),
    [("user@example.org", True), ("u@sub.example.org", True), ("example.org", False), ("user@example", False), ("@example.org", False)],
)
def test_validate_jid_format(jid, expected):
    assert validate_jid_format(jid) is expected


def test_validate_domain_ban():
    assert validate_domain_ban("*.example.org")[0] is True
    assert validate_domain_ban("example.org")[0] is True
    ok, message = validate_domain_ban("*.org")
    assert ok is False
    assert "too generic" in message


@pytest.mark.parametrize(
    ("user_domain", "banned_domain", "expected"),
    [
        ("example.org", "example.org", True),
        ("sub.example.org", "example.org", True),
        ("badexample.org", "example.org", False),
        (None, "example.org", False),
        ("example.org", None, False),
    ],
)
def test_domain_matches(user_domain, banned_domain, expected):
    assert domain_matches(user_domain, banned_domain) is expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [("example.org", True), ("*.example.org", False), ("user@example.org", False), ("nick", False), ("a/b", False)],
)
def test_looks_like_domain(value, expected):
    assert looks_like_domain(value) is expected


def test_normalize_ban_target_jid_domain_nick():
    assert normalize_ban_target(jid="User@Example.org/Res") == (
        "jid",
        "user@example.org",
        "user@example.org",
        None,
    )
    assert normalize_ban_target(jid="*.Example.org") == (
        "domain",
        "example.org",
        "*.example.org",
        None,
    )
    assert normalize_ban_target(nick=" SomeNick ") == ("nick", "somenick", None, "somenick")
    with pytest.raises(ValueError):
        normalize_ban_target()


def test_pagination_helpers():
    lines = [str(i) for i in range(25)]
    page_lines, current, total_pages, total_items = paginate_lines(lines, 2, per_page=10)
    assert page_lines == [str(i) for i in range(10, 20)]
    assert (current, total_pages, total_items) == (2, 3, 25)
    assert resolve_page(-1, 25, per_page=10) == 3
    assert resolve_page(99, 25, per_page=10) == 3
    assert resolve_page(0, 25, per_page=10) == 1



def test_all_pages_arg_helpers():
    assert wants_all_pages(["all"]) is True
    assert wants_all_pages(["2", "ALL"]) is True
    assert wants_all_pages(["small"]) is False
    assert without_all_pages_arg(["all", "spam", "ALL", "2"]) == ["spam", "2"]
