"""Focused RTBL apply tests for mutation-sensitive branches."""

from __future__ import annotations

import pytest

from banbot.rtbl_apply import RtblApplyMixin
from banbot.rtbl_utils import _rtbl_hash_jid
from banbot.utils import bare_jid, domain_matches


class RtblMutationBot(RtblApplyMixin):
    def __init__(self):
        self.rtbl_enabled = True
        self.rtbl_hash_cache = {}
        self.rtbl_domain_cache = {}
        self.ignore_jids = set()
        self.ignore_domains = set()
        self.protected_rooms = {"room@conference.example.test"}
        self.occupants = {
            "room@conference.example.test": {
                "Hash": {"jid": "hash@example.test/res"},
                "DomainOne": {"jid": "one@spam.example/res"},
                "DomainTwo": {"jid": "two@spam.example/res"},
                "NoJid": {},
                "NoAt": {"jid": "not-a-jid"},
                "Ignored": {"jid": "ignored@spam.example/res"},
            },
            "other@conference.example.test": {
                "Other": {"jid": "hash@example.test/res"},
            },
        }
        self.jid_bans = []
        self.domain_bans = []

    def bare_jid(self, jid):
        return bare_jid(jid)

    def _rtbl_hash_jid(self, jid):
        return _rtbl_hash_jid(jid)

    def is_ignored_jid(self, jid):
        return bare_jid(jid) in self.ignore_jids

    def is_ignored_domain(self, domain):
        domain = domain.lower().lstrip("*.")
        return domain in self.ignore_domains or any(
            domain_matches(domain, ignored) for ignored in self.ignore_domains
        )

    async def _rtbl_apply_ban_jid(self, jid, nick, reason):
        self.jid_bans.append((jid, nick, reason))

    async def _rtbl_apply_ban_domain(self, domain, reason, nick=None, jid=None):
        self.domain_bans.append((domain, reason, nick, jid))


@pytest.mark.asyncio
async def test_check_jid_against_rtbl_returns_false_when_disabled_or_invalid():
    bot = RtblMutationBot()
    bot.rtbl_hash_cache[_rtbl_hash_jid("hash@example.test")] = "hash reason"

    bot.rtbl_enabled = False
    assert await bot.check_jid_against_rtbl("hash@example.test/res", "Hash") is False
    assert bot.jid_bans == []

    bot.rtbl_enabled = True
    assert await bot.check_jid_against_rtbl("", "Hash") is False
    assert await bot.check_jid_against_rtbl("not-a-jid", "Hash") is False


@pytest.mark.asyncio
async def test_hash_publish_scan_checks_only_matching_hash_and_skips_ignored():
    bot = RtblMutationBot()
    bot.ignore_jids = {"ignored@spam.example"}

    await bot._rtbl_check_all_occupants_for_hash(_rtbl_hash_jid("hash@example.test"), "hash reason")

    assert bot.jid_bans == [("hash@example.test", "Hash", "hash reason")]


@pytest.mark.asyncio
async def test_domain_publish_scan_applies_once_for_first_matching_non_ignored_occupant():
    bot = RtblMutationBot()
    bot.ignore_jids = {"ignored@spam.example"}

    await bot._rtbl_check_all_occupants_for_domain("spam.example", "domain reason")

    assert bot.domain_bans == [("spam.example", "domain reason", "DomainOne", "one@spam.example")]


@pytest.mark.asyncio
async def test_full_scan_deduplicates_jids_and_domains_and_honors_domain_ignore():
    bot = RtblMutationBot()
    bot.rtbl_hash_cache[_rtbl_hash_jid("hash@example.test")] = "hash reason"
    bot.rtbl_domain_cache["spam.example"] = "domain reason"

    matched_jids, matched_domains = await bot._rtbl_check_all_occupants_against_caches("mutation-test")

    assert (matched_jids, matched_domains) == (1, 3)
    assert bot.jid_bans == [("hash@example.test", "Hash", "hash reason")]
    assert bot.domain_bans == [("spam.example", "domain reason", "DomainOne", "one@spam.example")]

    bot = RtblMutationBot()
    bot.rtbl_domain_cache["spam.example"] = "domain reason"
    bot.ignore_domains = {"spam.example"}
    assert await bot._rtbl_check_all_occupants_against_caches() == (0, 0)
    assert bot.domain_bans == []


@pytest.mark.asyncio
async def test_check_jid_against_rtbl_exact_ignore_blocks_hash_and_domain_bans():
    bot = RtblMutationBot()
    bot.rtbl_hash_cache[_rtbl_hash_jid("hash@example.test")] = "hash reason"
    bot.rtbl_domain_cache["example.test"] = "domain reason"
    bot.ignore_jids = {"hash@example.test"}

    assert await bot.check_jid_against_rtbl("hash@example.test/res", "Hash") is False
    assert bot.jid_bans == []
    assert bot.domain_bans == []


@pytest.mark.asyncio
async def test_check_jid_against_rtbl_domain_path_passes_original_context():
    bot = RtblMutationBot()
    bot.rtbl_domain_cache["spam.example"] = "domain reason"

    assert await bot.check_jid_against_rtbl("One@Spam.Example/Res", "DomainOne") is True
    assert bot.domain_bans == [("spam.example", "domain reason", "DomainOne", "one@spam.example")]
    assert bot.jid_bans == []


@pytest.mark.asyncio
async def test_check_jid_against_rtbl_hash_precedence_over_ignored_domain():
    bot = RtblMutationBot()
    bot.rtbl_hash_cache[_rtbl_hash_jid("one@spam.example")] = "hash reason"
    bot.rtbl_domain_cache["spam.example"] = "domain reason"
    bot.ignore_domains = {"spam.example"}

    assert await bot.check_jid_against_rtbl("one@spam.example/res", "DomainOne") is True
    assert bot.jid_bans == [("one@spam.example", "DomainOne", "hash reason")]
    assert bot.domain_bans == []


@pytest.mark.asyncio
async def test_full_scan_counts_all_domain_occupants_but_applies_each_domain_once():
    bot = RtblMutationBot()
    bot.occupants["room@conference.example.test"].update(
        {
            "DomainThree": {"jid": "three@spam.example/res"},
            "SubDomain": {"jid": "four@sub.spam.example/res"},
        }
    )
    bot.rtbl_domain_cache["spam.example"] = "domain reason"

    matched_jids, matched_domains = await bot._rtbl_check_all_occupants_against_caches("domain-only")

    assert matched_jids == 0
    assert matched_domains == 5
    assert bot.domain_bans == [("spam.example", "domain reason", "DomainOne", "one@spam.example")]


@pytest.mark.asyncio
async def test_hash_publish_scan_skips_unprotected_rooms_missing_jids_invalid_jids_and_duplicates():
    bot = RtblMutationBot()
    bot.occupants["room@conference.example.test"]["HashDuplicate"] = {"jid": "hash@example.test/other"}
    bot.occupants["room@conference.example.test"]["Upper"] = {"jid": "HASH@EXAMPLE.TEST/res"}

    await bot._rtbl_check_all_occupants_for_hash(_rtbl_hash_jid("hash@example.test"), "hash reason")

    assert bot.jid_bans == [
        ("hash@example.test", "Hash", "hash reason"),
        ("hash@example.test", "HashDuplicate", "hash reason"),
        ("hash@example.test", "Upper", "hash reason"),
    ]


@pytest.mark.asyncio
async def test_domain_publish_scan_returns_after_first_matching_occupant():
    bot = RtblMutationBot()
    bot.occupants["room@conference.example.test"]["Earlier"] = {"jid": "earlier@other.example/res"}
    bot.rtbl_domain_cache["spam.example"] = "unused"

    await bot._rtbl_check_all_occupants_for_domain("spam.example", "domain reason")

    assert bot.domain_bans == [("spam.example", "domain reason", "DomainOne", "one@spam.example")]


@pytest.mark.asyncio
async def test_hash_publish_scan_continues_past_unprotected_rooms():
    bot = RtblMutationBot()
    bot.occupants = {
        "unprotected@conference.example.test": {
            "IgnoredRoom": {"jid": "hash@example.test/res"},
        },
        "room@conference.example.test": {
            "Hash": {"jid": "hash@example.test/res"},
        },
    }

    await bot._rtbl_check_all_occupants_for_hash(
        _rtbl_hash_jid("hash@example.test"),
        "hash reason",
    )

    assert bot.jid_bans == [("hash@example.test", "Hash", "hash reason")]


@pytest.mark.asyncio
async def test_domain_publish_scan_continues_past_unprotected_rooms():
    bot = RtblMutationBot()
    bot.occupants = {
        "unprotected@conference.example.test": {
            "IgnoredRoom": {"jid": "one@spam.example/res"},
        },
        "room@conference.example.test": {
            "DomainOne": {"jid": "one@spam.example/res"},
        },
    }

    await bot._rtbl_check_all_occupants_for_domain("spam.example", "domain reason")

    assert bot.domain_bans == [("spam.example", "domain reason", "DomainOne", "one@spam.example")]


@pytest.mark.asyncio
async def test_domain_apply_wrapper_preserves_reason_argument():
    bot = RtblMutationBot()

    await bot._rtbl_apply_ban_domain("spam.example", "domain reason", nick="Nick", jid="jid@example.test")

    assert bot.domain_bans == [("spam.example", "domain reason", "Nick", "jid@example.test")]
