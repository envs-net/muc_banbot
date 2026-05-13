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
