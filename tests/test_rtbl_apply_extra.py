import pytest

from banbot.rtbl_apply import RtblApplyMixin
from banbot.rtbl_utils import _rtbl_hash_jid
from banbot.utils import bare_jid


class RtblApplyBot(RtblApplyMixin):
    def __init__(self):
        self.rtbl_enabled = True
        self.rtbl_hash_cache = {}
        self.rtbl_domain_cache = {}
        self.ignore_jids = set()
        self.ignore_domains = set()
        self.protected_rooms = {"room@conference.example.test"}
        self.occupants = {
            "room@conference.example.test": {
                "HashUser": {"jid": "hash@example.test/res"},
                "DomainUser": {"jid": "bad@spam.example/res"},
                "Ignored": {"jid": "ignored@example.test/res"},
            },
            "unprotected@conference.example.test": {
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
        return domain in self.ignore_domains

    async def _rtbl_apply_ban_jid(self, jid, nick, reason):
        self.jid_bans.append((jid, nick, reason))

    async def _rtbl_apply_ban_domain(self, domain, reason, nick=None, jid=None):
        self.domain_bans.append((domain, reason, nick, jid))


@pytest.mark.rtbl
@pytest.mark.asyncio
async def test_check_jid_against_rtbl_applies_hash_before_domain():
    bot = RtblApplyBot()
    bot.rtbl_hash_cache[_rtbl_hash_jid("user@example.test")] = "hash reason"
    bot.rtbl_domain_cache["example.test"] = "domain reason"

    matched = await bot.check_jid_against_rtbl("user@example.test/resource", "Nick")

    assert matched is True
    assert bot.jid_bans == [("user@example.test", "Nick", "hash reason")]
    assert bot.domain_bans == []


@pytest.mark.rtbl
@pytest.mark.asyncio
async def test_domain_ignore_suppresses_domain_but_not_hash_matches():
    bot = RtblApplyBot()
    bot.rtbl_hash_cache[_rtbl_hash_jid("hash@example.test")] = "hash reason"
    bot.rtbl_domain_cache["example.test"] = "domain reason"
    bot.ignore_domains = {"example.test"}

    assert await bot.check_jid_against_rtbl("hash@example.test/res", "HashUser") is True
    assert bot.jid_bans

    bot.jid_bans.clear()
    assert await bot.check_jid_against_rtbl("domain@example.test/res", "DomainUser") is False
    assert bot.domain_bans == []


@pytest.mark.rtbl
@pytest.mark.asyncio
async def test_scan_current_occupants_only_checks_protected_rooms_and_respects_ignorelist():
    bot = RtblApplyBot()
    bot.rtbl_hash_cache[_rtbl_hash_jid("hash@example.test")] = "hash reason"
    bot.rtbl_domain_cache["spam.example"] = "domain reason"
    bot.ignore_jids = {"ignored@example.test"}

    matched_hashes, matched_domains = await bot._rtbl_check_all_occupants_against_caches("test")

    assert matched_hashes == 1
    assert matched_domains == 1
    assert bot.jid_bans == [("hash@example.test", "HashUser", "hash reason")]
    assert bot.domain_bans == [("spam.example", "domain reason", "DomainUser", "bad@spam.example")]
