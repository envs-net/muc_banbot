"""Focused edge tests for utility helpers targeted by mutation testing."""

import pytest

from banbot.utils import (
    bare_jid,
    domain_matches,
    human_time,
    get_list_page_size,
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
    assert msg.startswith("❌ Domain")
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


def test_parse_duration_error_messages_are_specific():
    with pytest.raises(ValueError, match="Invalid duration format"):
        parse_duration("10w")
    with pytest.raises(ValueError, match="Invalid duration number"):
        parse_duration("xm")
    with pytest.raises(ValueError, match="greater than zero"):
        parse_duration("0m")
    with pytest.raises(ValueError, match="greater than zero"):
        parse_duration("-5m")


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("1s", 1),
        ("1S", 1),
        ("01m", 60),
        ("0002h", 7200),
        ("3D", 259200),
    ],
)
def test_parse_duration_numeric_boundaries_and_case(value, expected):
    assert parse_duration(value) == expected


def test_safe_jid_accepts_non_string_values_and_only_escapes_at_signs():
    assert safe_jid(None) == "None"
    assert safe_jid(123) == "123"
    assert safe_jid("@start@end@") == "@\u200bstart@\u200bend@\u200b"


@pytest.mark.parametrize(
    "jid",
    [
        None,
        "",
        "userexample.org",
        "user@@example.org",
        "user@example.org@evil.org",
        "@example.org",
        "user@",
        "user@example",
        "user@localhost",
    ],
)
def test_validate_jid_format_rejects_every_required_part_mutation(jid):
    assert validate_jid_format(jid) is False


@pytest.mark.parametrize(
    "jid",
    [
        "a@b.cd",
        "user.name+tag@sub.example.org",
        "UPPER@Example.Org",
    ],
)
def test_validate_jid_format_accepts_basic_two_part_domains(jid):
    assert validate_jid_format(jid) is True


@pytest.mark.parametrize(
    ("domain", "message_part"),
    [
        ("", "*."),
        (".", "*."),
        ("org", "*.org"),
        ("*.org", "*.org"),
        ("localhost", "*.localhost"),
        ("*.", "*."),
    ],
)
def test_validate_domain_ban_rejection_message_contains_normalized_target(domain, message_part):
    ok, message = validate_domain_ban(domain)
    assert ok is False
    assert message_part in message
    assert "too generic" in message


@pytest.mark.parametrize(
    "domain",
    [
        "example.org",
        "*.example.org",
        "Example.Org.",
        "..example.org..",
        "*.deep.sub.example.org",
    ],
)
def test_validate_domain_ban_accepts_two_or_more_labels_after_cleanup(domain):
    ok, message = validate_domain_ban(domain)
    assert ok is True
    assert message == ""


@pytest.mark.parametrize(
    ("user_domain", "banned_domain"),
    [
        ("example.org", "example.org"),
        ("sub.example.org", "example.org"),
        ("deep.sub.example.org.", "example.org."),
        ("EXAMPLE.ORG", "example.org"),
    ],
)
def test_domain_matches_positive_boundary_cases(user_domain, banned_domain):
    assert domain_matches(user_domain, banned_domain) is True


@pytest.mark.parametrize(
    ("user_domain", "banned_domain"),
    [
        (None, "example.org"),
        ("example.org", None),
        ("", "example.org"),
        ("example.org", ""),
        ("badexample.org", "example.org"),
        ("example.org.evil", "example.org"),
        ("subexample.org", "example.org"),
    ],
)
def test_domain_matches_negative_boundary_cases(user_domain, banned_domain):
    assert domain_matches(user_domain, banned_domain) is False


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (" example.org ", True),
        ("sub.example.org", True),
        ("example", False),
        ("*.example.org", False),
        ("user@example.org", False),
        ("example.org/resource", False),
        ("", False),
        (None, False),
    ],
)
def test_looks_like_domain_requires_plain_bare_domain_shape(value, expected):
    assert looks_like_domain(value) is expected


def test_normalize_ban_target_empty_jid_falls_back_to_nick():
    assert normalize_ban_target(jid="", nick=" Nick ") == (
        "nick",
        "nick",
        None,
        "nick",
    )


def test_normalize_ban_target_domain_strips_target_but_preserves_normalized_jid_marker():
    target_type, target, normalized_jid, normalized_nick = normalize_ban_target(
        jid="*.Example.Org.",
        nick=" Reporter ",
    )
    assert target_type == "domain"
    assert target == "example.org"
    assert normalized_jid == "*.example.org."
    assert normalized_nick == "reporter"


def test_paginate_lines_exact_multiple_and_single_item_pages():
    lines = ["a", "b", "c", "d"]
    assert paginate_lines(lines, 1, per_page=2) == (["a", "b"], 1, 2, 4)
    assert paginate_lines(lines, 2, per_page=2) == (["c", "d"], 2, 2, 4)
    assert paginate_lines(lines, 3, per_page=2) == (["c", "d"], 2, 2, 4)
    assert paginate_lines(lines, 2, per_page=1) == (["b"], 2, 4, 4)


def test_resolve_page_clamps_low_high_and_last_for_exact_multiples():
    assert resolve_page(-1, 20, per_page=10) == 2
    assert resolve_page(0, 20, per_page=10) == 1
    assert resolve_page(99, 20, per_page=10) == 2
    assert resolve_page(2, 20, per_page=10) == 2


def test_parse_duration_rejects_single_suffix_without_number():
    for value in ["s", "m", "h", "d"]:
        with pytest.raises(ValueError):
            parse_duration(value)


def test_parse_duration_accepts_python_integer_whitespace_but_not_bad_suffix_spacing():
    assert parse_duration(" 1m") == 60
    assert parse_duration("\t2h") == 7200
    assert parse_duration("1\tm") == 60
    with pytest.raises(ValueError):
        parse_duration("1m ")


def test_parse_duration_distinguishes_units_and_does_not_round_or_clamp():
    assert parse_duration("59s") == 59
    assert parse_duration("2m") == 120
    assert parse_duration("2h") == 7200
    assert parse_duration("2d") == 172800
    assert parse_duration("999s") == 999


def test_safe_jid_escapes_every_at_sign_exactly_once():
    value = "one@two@three@example.org"
    escaped = safe_jid(value)
    assert escaped == "one@\u200btwo@\u200bthree@\u200bexample.org"
    assert escaped.count("@") == value.count("@")
    assert escaped.count("\u200b") == value.count("@")


@pytest.mark.parametrize(
    ("domain", "expected_message_fragment"),
    [
        ("*.example", "*.example"),
        ("..", "*."),
        ("*.localhost.", "*.localhost"),
        (" . ", "*."),
    ],
)
def test_validate_domain_ban_rejects_after_wildcard_dot_and_empty_part_cleanup(domain, expected_message_fragment):
    ok, message = validate_domain_ban(domain)
    assert ok is False
    assert expected_message_fragment in message
    assert message.startswith("❌ Domain")


@pytest.mark.parametrize(
    ("user_domain", "banned_domain"),
    [
        ("reallyexample.org", "example.org"),
        ("sub.example.org.evil", "example.org"),
        ("evil-example.org", "example.org"),
        ("example.org", "sub.example.org"),
    ],
)
def test_domain_matches_requires_exact_domain_or_dot_boundary(user_domain, banned_domain):
    assert domain_matches(user_domain, banned_domain) is False


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (" example.org ", True),
        (" example ", False),
        ("*.example.org ", False),
        ("user@sub.example.org", False),
        ("sub.example.org/resource", False),
        ("sub.example.org/", False),
    ],
)
def test_looks_like_domain_rejects_wildcards_jids_resources_and_single_labels(value, expected):
    assert looks_like_domain(value) is expected


def test_normalize_ban_target_domain_without_nick_and_jid_resource_cases_are_distinct():
    assert normalize_ban_target(jid="*.Sub.Example.Org.") == (
        "domain",
        "sub.example.org",
        "*.sub.example.org.",
        None,
    )
    assert normalize_ban_target(jid="*.Sub.Example.Org..") == (
        "domain",
        "sub.example.org",
        "*.sub.example.org..",
        None,
    )
    assert normalize_ban_target(jid="User@Example.Org/Device/Extra") == (
        "jid",
        "user@example.org",
        "user@example.org",
        None,
    )
    with pytest.raises(ValueError, match="Ban target requires"):
        normalize_ban_target(jid=None, nick="")


def test_paginate_lines_uses_start_offset_and_end_exclusively():
    lines = [str(i) for i in range(1, 8)]
    assert paginate_lines(lines, 1, per_page=3) == (["1", "2", "3"], 1, 3, 7)
    assert paginate_lines(lines, 2, per_page=3) == (["4", "5", "6"], 2, 3, 7)
    assert paginate_lines(lines, 3, per_page=3) == (["7"], 3, 3, 7)


def test_resolve_page_uses_ceiling_total_pages_and_clamps_to_last():
    assert resolve_page(-1, 1, per_page=10) == 1
    assert resolve_page(-1, 10, per_page=10) == 1
    assert resolve_page(-1, 11, per_page=10) == 2
    assert resolve_page(99, 11, per_page=10) == 2



def test_get_list_page_size_prefers_object_value_over_config_and_clamps_minimum(monkeypatch):
    import config

    monkeypatch.setattr(config, "LIST_PAGE_SIZE", 25, raising=False)

    class Bot:
        list_page_size = "7"

    class ZeroBot:
        list_page_size = 0

    class NegativeBot:
        list_page_size = -5

    assert get_list_page_size(Bot()) == 7
    assert get_list_page_size(ZeroBot()) == 1
    assert get_list_page_size(NegativeBot()) == 1


def test_get_list_page_size_uses_config_value_and_handles_invalid_config(monkeypatch):
    import config

    monkeypatch.setattr(config, "LIST_PAGE_SIZE", "12", raising=False)
    assert get_list_page_size() == 12

    monkeypatch.setattr(config, "LIST_PAGE_SIZE", "bad", raising=False)
    assert get_list_page_size(default=9) == 9

    monkeypatch.setattr(config, "LIST_PAGE_SIZE", 0, raising=False)
    assert get_list_page_size(default=9) == 1


def test_get_list_page_size_falls_back_when_config_import_fails(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def failing_import(name, *args, **kwargs):
        if name == "config":
            raise RuntimeError("config unavailable")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", failing_import)
    assert get_list_page_size(default=13) == 13


def test_parse_duration_keeps_error_classes_for_one_character_and_two_character_inputs():
    with pytest.raises(ValueError, match="Invalid duration format"):
        parse_duration("m")
    with pytest.raises(ValueError, match="Invalid duration number"):
        parse_duration("xm")
    assert parse_duration("1m") == 60


def test_normalize_ban_target_rejects_blank_nick_after_strip():
    with pytest.raises(ValueError, match="Ban target requires"):
        normalize_ban_target(nick="   ")


def test_validate_domain_ban_trims_wildcard_and_outer_dots_before_counting_labels():
    assert validate_domain_ban("*.sub.example.org.") == (True, "")
    ok, message = validate_domain_ban("*.org.")
    assert ok is False
    assert "*.org" in message


def test_domain_matches_empty_after_stripping_dots_does_not_match_everything():
    assert domain_matches("example.org", ".") is False
    assert domain_matches(".", "example.org") is False


def test_get_list_page_size_invalid_object_value_uses_default_without_config(monkeypatch):
    import config

    monkeypatch.setattr(config, "LIST_PAGE_SIZE", 25, raising=False)

    class BadBot:
        list_page_size = "not-an-int"

    assert get_list_page_size(BadBot(), default=14) == 14


def test_get_list_page_size_object_none_falls_back_to_config(monkeypatch):
    import config

    monkeypatch.setattr(config, "LIST_PAGE_SIZE", "16", raising=False)

    class NoneBot:
        list_page_size = None

    assert get_list_page_size(NoneBot(), default=14) == 16


def test_paginate_lines_page_size_larger_than_total_and_resolve_mid_page():
    lines = ["one", "two"]
    assert paginate_lines(lines, 1, per_page=99) == (["one", "two"], 1, 1, 2)
    assert resolve_page(2, 25, per_page=10) == 2


def test_validate_domain_ban_keeps_double_wildcard_generic_after_cleanup():
    ok, message = validate_domain_ban("**.org")
    assert ok is True
    assert message == ""


def test_domain_matches_does_not_match_when_banned_domain_only_becomes_empty_after_strip():
    assert domain_matches(".", ".") is False
    assert domain_matches("sub.example.org", "...") is False
