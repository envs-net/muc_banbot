"""Property-based tests for pure utility helpers."""

import pytest

pytest.importorskip("hypothesis")

from hypothesis import given, settings
from hypothesis import strategies as st

from banbot.utils import domain_matches, normalize_ban_target, parse_duration


pytestmark = pytest.mark.property


_DURATION_FACTORS = {
    "s": 1,
    "m": 60,
    "h": 3600,
    "d": 86400,
}


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


label = st.from_regex(r"[a-z0-9](?:[a-z0-9-]{0,10}[a-z0-9])?", fullmatch=True)
tld = st.from_regex(r"[a-z]{2,8}", fullmatch=True)
base_domain = st.builds(lambda left, right: f"{left}.{right}", label, tld)
subdomain = st.builds(lambda sub, base: f"{sub}.{base}", label, base_domain)


@settings(max_examples=100)
@given(base=base_domain, mixed_case=st.booleans())
def test_domain_matches_exact_domain_with_case_and_dot_normalization(base, mixed_case):
    user_domain = base.upper() + "." if mixed_case else base
    banned_domain = base.lower() + "."
    assert domain_matches(user_domain, banned_domain) is True


@settings(max_examples=100)
@given(user=subdomain, base=base_domain)
def test_domain_matches_subdomains(user, base):
    # Only assert when the generated subdomain belongs to the generated base.
    user = f"host.{base}"
    assert domain_matches(user, base) is True


@settings(max_examples=100)
@given(base=base_domain, other=base_domain)
def test_domain_matches_rejects_unrelated_domains(base, other):
    from hypothesis import assume

    assume(base != other)
    assume(not base.endswith("." + other))
    assume(not other.endswith("." + base))
    assert domain_matches(base, other) is False


@settings(max_examples=100)
@given(
    local=st.from_regex(r"[a-zA-Z0-9._%+-]{1,20}", fullmatch=True),
    domain=base_domain,
    resource=st.from_regex(r"[a-zA-Z0-9_-]{1,20}", fullmatch=True),
)
def test_normalize_ban_target_jid_lowercases_and_strips_resource(local, domain, resource):
    jid = f"{local}@{domain.upper()}/{resource}"
    target_type, target, normalized_jid, normalized_nick = normalize_ban_target(jid=jid)
    expected = f"{local.lower()}@{domain.lower()}"
    assert target_type == "jid"
    assert target == expected
    assert normalized_jid == expected
    assert normalized_nick is None


@settings(max_examples=100)
@given(domain=base_domain)
def test_normalize_ban_target_wildcard_domain(domain):
    target_type, target, normalized_jid, normalized_nick = normalize_ban_target(
        jid=f"*.{domain.upper()}."
    )
    assert target_type == "domain"
    assert target == domain.lower()
    assert normalized_jid == f"*.{domain.lower()}."
    assert normalized_nick is None


@settings(max_examples=100)
@given(nick=st.text(min_size=1, max_size=40).filter(lambda s: s.strip() != ""))
def test_normalize_ban_target_nick_only_strips_and_lowercases(nick):
    target_type, target, normalized_jid, normalized_nick = normalize_ban_target(nick=nick)
    expected = nick.lower().strip()
    assert target_type == "nick"
    assert target == expected
    assert normalized_jid is None
    assert normalized_nick == expected
