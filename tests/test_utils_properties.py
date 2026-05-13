"""Property-based tests for pure utility helpers."""

import re

import pytest

pytest.importorskip("hypothesis")

from hypothesis import assume, given, settings
from hypothesis import strategies as st

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


pytestmark = pytest.mark.property


_DURATION_FACTORS = {
    "s": 1,
    "m": 60,
    "h": 3600,
    "d": 86400,
}

label = st.from_regex(r"[a-z0-9](?:[a-z0-9-]{0,10}[a-z0-9])?", fullmatch=True)
tld = st.from_regex(r"[a-z]{2,8}", fullmatch=True)
base_domain = st.builds(lambda left, right: f"{left}.{right}", label, tld)
subdomain = st.builds(lambda sub, base: f"{sub}.{base}", label, base_domain)
localpart = st.from_regex(r"[a-zA-Z0-9._%+-]{1,20}", fullmatch=True)
resource = st.from_regex(r"[a-zA-Z0-9_-]{1,20}", fullmatch=True)
non_blank_text = st.text(min_size=1, max_size=40).filter(lambda s: s.strip() != "")


@settings(max_examples=100)
@given(
    value=st.integers(min_value=1, max_value=10**7),
    unit=st.sampled_from(list(_DURATION_FACTORS.keys())),
    upper=st.booleans(),
)
def test_parse_duration_accepts_positive_integer_with_supported_unit(value, unit, upper):
    suffix = unit.upper() if upper else unit
    assert parse_duration(f"{value}{suffix}") == value * _DURATION_FACTORS[unit]


@settings(max_examples=100)
@given(
    value=st.integers(max_value=0),
    unit=st.sampled_from(list(_DURATION_FACTORS.keys())),
)
def test_parse_duration_rejects_non_positive_values(value, unit):
    with pytest.raises(ValueError):
        parse_duration(f"{value}{unit}")


@settings(max_examples=100)
@given(
    text=st.text(min_size=0, max_size=20).filter(
        lambda s: len(s) < 2 or s[-1:].lower() not in _DURATION_FACTORS
    )
)
def test_parse_duration_rejects_missing_or_unknown_units(text):
    with pytest.raises(ValueError):
        parse_duration(text)


@settings(max_examples=100)
@given(seconds=st.integers(min_value=1, max_value=10 * 365 * 86400))
def test_human_time_roundtrips_to_original_seconds(seconds):
    rendered = human_time(seconds)
    total = 0
    seen_units = []
    for part in rendered.split():
        match = re.fullmatch(r"([1-9][0-9]*)([dhms])", part)
        assert match is not None
        value, unit = match.groups()
        seen_units.append(unit)
        total += int(value) * _DURATION_FACTORS[unit]

    assert total == seconds
    assert seen_units == sorted(seen_units, key="dhms".index)


@settings(max_examples=100)
@given(text=st.one_of(st.text(max_size=80), st.integers(), st.none()))
def test_safe_jid_preserves_string_content_except_at_escaping(text):
    original = str(text)
    escaped = safe_jid(text)
    assert escaped.replace("@\u200b", "@") == original
    assert escaped.count("@") == original.count("@")
    for index, char in enumerate(escaped):
        if char == "@":
            assert escaped[index + 1 : index + 2] == "\u200b"


@settings(max_examples=100)
@given(local=localpart, domain=base_domain, res=resource)
def test_bare_jid_always_lowercases_and_removes_resource(local, domain, res):
    jid = f"{local}@{domain.upper()}/{res}"
    assert bare_jid(jid) == f"{local.lower()}@{domain.lower()}"


@settings(max_examples=100)
@given(local=localpart, domain=base_domain)
def test_validate_jid_format_accepts_generated_bare_jids(local, domain):
    assert validate_jid_format(f"{local}@{domain}") is True


@settings(max_examples=100)
@given(local=localpart, domain=base_domain)
def test_validate_jid_format_rejects_extra_at_signs(local, domain):
    assert validate_jid_format(f"{local}@{domain}@{domain}") is False


@settings(max_examples=100)
@given(domain=base_domain, wildcard=st.booleans(), dotted=st.booleans())
def test_validate_domain_ban_accepts_generated_precise_domains(domain, wildcard, dotted):
    value = f"*.{domain}" if wildcard else domain
    if dotted:
        value = f"..{value}."
    ok, message = validate_domain_ban(value.upper())
    assert ok is True
    assert message == ""


@settings(max_examples=100)
@given(single_label=label)
def test_validate_domain_ban_rejects_single_label_domains(single_label):
    ok, message = validate_domain_ban(single_label)
    assert ok is False
    assert "too generic" in message


@settings(max_examples=100)
@given(base=base_domain, mixed_case=st.booleans())
def test_domain_matches_exact_domain_with_case_and_dot_normalization(base, mixed_case):
    user_domain = base.upper() + "." if mixed_case else base
    banned_domain = base.lower() + "."
    assert domain_matches(user_domain, banned_domain) is True


@settings(max_examples=100)
@given(base=base_domain)
def test_domain_matches_subdomains(base):
    assert domain_matches(f"host.{base}", base) is True


@settings(max_examples=100)
@given(base=base_domain, other=base_domain)
def test_domain_matches_rejects_unrelated_domains(base, other):
    assume(base != other)
    assume(not base.endswith("." + other))
    assume(not other.endswith("." + base))
    assert domain_matches(base, other) is False


@settings(max_examples=100)
@given(prefix=label, base=base_domain)
def test_domain_matches_rejects_suffix_without_label_boundary(prefix, base):
    assert domain_matches(f"{prefix}{base}", base) is False


@settings(max_examples=100)
@given(domain=base_domain)
def test_looks_like_domain_accepts_plain_domains_and_rejects_jids_or_wildcards(domain):
    assert looks_like_domain(f" {domain.upper()} ") is True
    assert looks_like_domain(f"user@{domain}") is False
    assert looks_like_domain(f"*.{domain}") is False
    assert looks_like_domain(f"{domain}/resource") is False


@settings(max_examples=100)
@given(
    local=localpart,
    domain=base_domain,
    res=resource,
    nick=st.one_of(st.none(), non_blank_text),
)
def test_normalize_ban_target_jid_lowercases_and_strips_resource(local, domain, res, nick):
    jid = f"{local}@{domain.upper()}/{res}"
    target_type, target, normalized_jid, normalized_nick = normalize_ban_target(jid=jid, nick=nick)
    expected = f"{local.lower()}@{domain.lower()}"
    assert target_type == "jid"
    assert target == expected
    assert normalized_jid == expected
    assert normalized_nick == (nick.lower().strip() if nick else None)


@settings(max_examples=100)
@given(domain=base_domain, nick=st.one_of(st.none(), non_blank_text))
def test_normalize_ban_target_wildcard_domain(domain, nick):
    target_type, target, normalized_jid, normalized_nick = normalize_ban_target(
        jid=f"*.{domain.upper()}.", nick=nick
    )
    assert target_type == "domain"
    assert target == domain.lower()
    assert normalized_jid == f"*.{domain.lower()}."
    assert normalized_nick == (nick.lower().strip() if nick else None)


@settings(max_examples=100)
@given(nick=non_blank_text)
def test_normalize_ban_target_nick_only_strips_and_lowercases(nick):
    target_type, target, normalized_jid, normalized_nick = normalize_ban_target(nick=nick)
    expected = nick.lower().strip()
    assert target_type == "nick"
    assert target == expected
    assert normalized_jid is None
    assert normalized_nick == expected


@settings(max_examples=100)
@given(
    args=st.lists(st.text(min_size=0, max_size=12), max_size=20),
    marker_case=st.sampled_from(["all", "ALL", "All", "aLl"]),
)
def test_all_page_helpers_detect_and_remove_standalone_all_case_insensitively(args, marker_case):
    combined = args + [marker_case]
    assert wants_all_pages(combined) is True
    filtered = without_all_pages_arg(combined)
    assert all(str(arg).lower() != "all" for arg in filtered)
    assert filtered == [arg for arg in args if str(arg).lower() != "all"]


@settings(max_examples=100)
@given(
    lines=st.lists(st.text(max_size=20), max_size=50),
    page=st.integers(min_value=-10, max_value=100),
    per_page=st.integers(min_value=1, max_value=20),
)
def test_paginate_lines_returns_valid_slice_and_metadata(lines, page, per_page):
    page_lines, current_page, total_pages, total_items = paginate_lines(lines, page, per_page)
    assert total_items == len(lines)
    assert total_pages == max(1, (len(lines) + per_page - 1) // per_page)
    assert 1 <= current_page <= total_pages
    assert len(page_lines) <= per_page
    assert page_lines == lines[(current_page - 1) * per_page : current_page * per_page]


@settings(max_examples=100)
@given(
    total_items=st.integers(min_value=0, max_value=500),
    page=st.integers(min_value=-10, max_value=200),
    per_page=st.integers(min_value=1, max_value=50),
)
def test_resolve_page_matches_paginate_lines_current_page(total_items, page, per_page):
    lines = [str(i) for i in range(total_items)]
    _, current_page, _, _ = paginate_lines(lines, page, per_page)
    assert resolve_page(page, total_items, per_page) == current_page
    assert resolve_page(-1, total_items, per_page) == max(1, (total_items + per_page - 1) // per_page)
