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


@pytest.mark.asyncio
async def test_check_jid_against_rtbl_domain_cache_order_and_exact_reason():
    bot = RtblMutationBot()
    bot.rtbl_domain_cache = {
        "other.example": "wrong reason",
        "spam.example": "right reason",
    }

    assert await bot.check_jid_against_rtbl("user@sub.spam.example/res", "User") is True
    assert bot.domain_bans == [("spam.example", "right reason", "User", "user@sub.spam.example")]
    assert bot.jid_bans == []


@pytest.mark.asyncio
async def test_check_jid_against_rtbl_hash_wins_before_domain_and_uses_bare_jid():
    bot = RtblMutationBot()
    bot.rtbl_hash_cache[_rtbl_hash_jid("one@spam.example")] = "hash reason"
    bot.rtbl_domain_cache["spam.example"] = "domain reason"

    assert await bot.check_jid_against_rtbl("One@Spam.Example/Resource", "Nick") is True
    assert bot.jid_bans == [("one@spam.example", "Nick", "hash reason")]
    assert bot.domain_bans == []


@pytest.mark.asyncio
async def test_check_all_occupants_against_caches_skips_disabled_and_unprotected_only():
    bot = RtblMutationBot()
    bot.rtbl_enabled = False
    bot.rtbl_hash_cache[_rtbl_hash_jid("hash@example.test")] = "hash reason"
    assert await bot._rtbl_check_all_occupants_against_caches("disabled") == (0, 0)

    bot = RtblMutationBot()
    bot.protected_rooms = set()
    bot.rtbl_hash_cache[_rtbl_hash_jid("hash@example.test")] = "hash reason"
    bot.rtbl_domain_cache["spam.example"] = "domain reason"
    assert await bot._rtbl_check_all_occupants_against_caches("no rooms") == (0, 0)
    assert bot.jid_bans == []
    assert bot.domain_bans == []


@pytest.mark.asyncio
async def test_check_all_occupants_against_caches_hash_prevents_domain_count_for_same_jid():
    bot = RtblMutationBot()
    bot.rtbl_hash_cache[_rtbl_hash_jid("one@spam.example")] = "hash reason"
    bot.rtbl_domain_cache["spam.example"] = "domain reason"

    matched_jids, matched_domains = await bot._rtbl_check_all_occupants_against_caches("precedence")

    assert (matched_jids, matched_domains) == (1, 2)
    assert bot.jid_bans == [("one@spam.example", "DomainOne", "hash reason")]
    assert bot.domain_bans == [("spam.example", "domain reason", "DomainTwo", "two@spam.example")]


@pytest.mark.asyncio
async def test_hash_publish_scan_no_match_or_ignored_match_does_not_apply():
    bot = RtblMutationBot()
    bot.ignore_jids = {"hash@example.test"}

    await bot._rtbl_check_all_occupants_for_hash(_rtbl_hash_jid("hash@example.test"), "hash reason")
    await bot._rtbl_check_all_occupants_for_hash(_rtbl_hash_jid("missing@example.test"), "missing")

    assert bot.jid_bans == []


@pytest.mark.asyncio
async def test_domain_publish_scan_no_match_ignored_jid_and_ignored_domain_do_not_apply():
    bot = RtblMutationBot()
    bot.ignore_jids = {"one@spam.example", "two@spam.example", "ignored@spam.example"}

    await bot._rtbl_check_all_occupants_for_domain("spam.example", "domain reason")
    assert bot.domain_bans == []

    bot = RtblMutationBot()
    bot.ignore_domains = {"spam.example"}
    await bot._rtbl_check_all_occupants_for_domain("spam.example", "domain reason")
    assert bot.domain_bans == []

    bot = RtblMutationBot()
    await bot._rtbl_check_all_occupants_for_domain("missing.example", "domain reason")
    assert bot.domain_bans == []


@pytest.mark.asyncio
async def test_domain_publish_scan_matches_subdomain_and_passes_bare_context():
    bot = RtblMutationBot()
    bot.occupants["room@conference.example.test"] = {
        "Sub": {"jid": "user@sub.spam.example/res"},
    }

    await bot._rtbl_check_all_occupants_for_domain("spam.example", "domain reason")

    assert bot.domain_bans == [("spam.example", "domain reason", "Sub", "user@sub.spam.example")]


class _FakeExecuteResult:
    def __init__(self, rows=None):
        self.rows = rows or []

    def __await__(self):
        async def _return_self():
            return self

        return _return_self().__await__()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def fetchall(self):
        return self.rows


class _FakeRtblDb:
    def __init__(self):
        self.execute_calls = []
        self.commit_count = 0
        self.cleanup_rows = []

    def execute(self, query, params=None):
        self.execute_calls.append((query, params))
        rows = self.cleanup_rows if query.strip().upper().startswith("SELECT") else []
        return _FakeExecuteResult(rows)

    async def commit(self):
        self.commit_count += 1


class LockedRtblMutationBot(RtblApplyMixin):
    def __init__(self):
        self.rtbl_announce = True
        self.rtbl_hash_cache = {}
        self.rtbl_domain_cache = {}
        self.ignore_jids = set()
        self.ignore_domains = set()
        self.protected_jids = set()
        self.protected_rooms = {"room@conference.example.test", "second@conference.example.test"}
        self.occupants = {
            "room@conference.example.test": {
                "Spam": {"jid": "spam@bad.example/res"},
                "Sub": {"jid": "sub@sub.bad.example/res"},
                "IgnoredJid": {"jid": "ignored@bad.example/res"},
                "IgnoredDomain": {"jid": "ignored@ignored.bad.example/res"},
                "Protected": {"jid": "admin@bad.example/res"},
                "NoJid": {},
                "NoAt": {"jid": "not-a-jid"},
                "Other": {"jid": "other@good.example/res"},
            },
            "second@conference.example.test": {
                "SpamAgain": {"jid": "spam@bad.example/other"},
                "Second": {"jid": "second@bad.example/res"},
            },
        }
        self.db = _FakeRtblDb()
        self.sent = []
        self.upserts = []
        self.applied = []
        self.events = []
        self.audit_events = []
        self.removed_domains = []
        self.unbanned = []
        self.fail_apply_for = set()

    def bare_jid(self, jid):
        return bare_jid(jid)

    def _rtbl_hash_jid(self, jid):
        return _rtbl_hash_jid(jid)

    def is_ignored_jid(self, jid):
        return bare_jid(jid) in self.ignore_jids

    def is_ignored_domain(self, domain):
        domain = domain.lower().lstrip("*.").strip(".")
        return domain in self.ignore_domains or any(
            domain_matches(domain, ignored) for ignored in self.ignore_domains
        )

    async def is_protected_admin_target(self, target, nick=None, jid=None):
        candidate = bare_jid(jid or target)
        if candidate in self.protected_jids:
            return True, f"{candidate} protected"
        return False, None

    async def bot_send_message(self, **kwargs):
        self.sent.append(kwargs)

    async def upsert_ban_db(self, jid, nick, until, issuer, comment):
        self.upserts.append((jid, nick, until, issuer, comment))

    def log_event(self, level, event, **fields):
        self.events.append((level, event, fields))

    async def audit_event(self, event_type, **kwargs):
        self.audit_events.append((event_type, kwargs))

    async def apply_ban_to_room(self, room, ban_jid, ban_nick, comment, issuer=None):
        if ban_jid in self.fail_apply_for:
            raise RuntimeError(f"failed for {ban_jid}")
        self.applied.append((room, ban_jid, ban_nick, comment, issuer))

    def _remove_domain_bans_from_cache(self, domain):
        self.removed_domains.append(domain)

    async def unban_all(self, target, issuer="rtbl_cleanup"):
        self.unbanned.append((target, issuer))


@pytest.mark.asyncio
async def test_jid_locked_ignored_target_announces_and_stops_before_db(monkeypatch):
    import config

    monkeypatch.setattr(config, "ADMIN_ROOM", "admin@conference.example.test", raising=False)
    bot = LockedRtblMutationBot()
    bot.ignore_jids = {"bad@example.test"}

    await bot._rtbl_apply_ban_jid_locked("bad@example.test", "Bad", "listed")

    assert bot.upserts == []
    assert bot.db.commit_count == 0
    assert bot.applied == []
    assert len(bot.sent) == 1
    assert "Ignored ban for bad@example.test" in bot.sent[0]["mbody"]
    assert bot.sent[0]["mto"] == "admin@conference.example.test"


@pytest.mark.asyncio
async def test_jid_locked_protected_target_announces_and_stops_before_db(monkeypatch):
    import config

    monkeypatch.setattr(config, "ADMIN_ROOM", "admin@conference.example.test", raising=False)
    bot = LockedRtblMutationBot()
    bot.protected_jids = {"bad@example.test"}

    await bot._rtbl_apply_ban_jid_locked("bad@example.test", "Bad", "listed")

    assert bot.upserts == []
    assert bot.db.commit_count == 0
    assert bot.applied == []
    assert "protected admin/owner" in bot.sent[0]["mbody"]
    assert "bad@example.test protected" in bot.sent[0]["mbody"]


@pytest.mark.asyncio
async def test_jid_locked_without_reason_persists_plain_comment_and_applies_all_rooms(monkeypatch):
    import config

    monkeypatch.setattr(config, "ADMIN_ROOM", "admin@conference.example.test", raising=False)
    bot = LockedRtblMutationBot()
    bot.rtbl_announce = False

    await bot._rtbl_apply_ban_jid_locked("bad@example.test", None, None)

    assert bot.upserts == [("bad@example.test", None, 0, "rtbl", "RTBL ban")]
    assert bot.db.commit_count == 1
    assert bot.sent == []
    assert sorted(bot.applied) == sorted(
        [
            ("room@conference.example.test", "bad@example.test", None, "RTBL ban", "rtbl"),
            ("second@conference.example.test", "bad@example.test", None, "RTBL ban", "rtbl"),
        ]
    )
    assert bot.events[0][1] == "rtbl_ban_applied"
    assert bot.events[0][2]["comment"] == "RTBL ban"
    assert bot.audit_events[0] == (
        "rtbl_ban_applied",
        {
            "actor": "rtbl",
            "target_type": "jid",
            "target": "bad@example.test",
            "jid": "bad@example.test",
            "nick": None,
            "comment": "RTBL ban",
        },
    )


@pytest.mark.asyncio
async def test_jid_locked_apply_failure_is_logged_without_rolling_back_persistence(monkeypatch, caplog):
    import config

    monkeypatch.setattr(config, "ADMIN_ROOM", "admin@conference.example.test", raising=False)
    bot = LockedRtblMutationBot()
    bot.fail_apply_for = {"bad@example.test"}

    with caplog.at_level("WARNING", logger="banbot.rtbl_apply"):
        await bot._rtbl_apply_ban_jid_locked("bad@example.test", "Bad", "listed")

    assert bot.upserts == [("bad@example.test", "Bad", 0, "rtbl", "RTBL: listed")]
    assert bot.db.commit_count == 1
    assert bot.audit_events[0][0] == "rtbl_ban_applied"
    assert "Failed to apply JID ban" in caplog.text


@pytest.mark.asyncio
async def test_domain_locked_ignored_domain_announces_and_does_not_scan_or_persist(monkeypatch):
    import config

    monkeypatch.setattr(config, "ADMIN_ROOM", "admin@conference.example.test", raising=False)
    bot = LockedRtblMutationBot()
    bot.ignore_domains = {"bad.example"}

    await bot._rtbl_apply_ban_domain_locked("*.Bad.Example.", "wave")

    assert bot.upserts == []
    assert bot.db.execute_calls == []
    assert bot.db.commit_count == 0
    assert bot.applied == []
    assert "Ignored domain ban *.bad.example." in bot.sent[0]["mbody"]


@pytest.mark.asyncio
async def test_domain_locked_matches_unique_bare_jids_removes_legacy_domain_and_announces(monkeypatch):
    import config

    monkeypatch.setattr(config, "ADMIN_ROOM", "admin@conference.example.test", raising=False)
    bot = LockedRtblMutationBot()
    bot.ignore_jids = {"ignored@bad.example"}
    bot.ignore_domains = {"ignored.bad.example"}
    bot.protected_jids = {"admin@bad.example"}
    bot.protected_rooms = ["room@conference.example.test", "second@conference.example.test"]

    await bot._rtbl_apply_ban_domain_locked("bad.example", "wave")

    assert bot.removed_domains == ["bad.example"]
    assert bot.db.execute_calls == [
        (
            "DELETE FROM bans WHERE issuer = 'rtbl' AND target_type = 'domain' AND target = ?",
            ("bad.example",),
        )
    ]
    assert sorted(bot.upserts) == sorted(
        [
            ("spam@bad.example", "Spam", 0, "rtbl", "RTBL domain ban: *.bad.example — wave"),
            ("sub@sub.bad.example", "Sub", 0, "rtbl", "RTBL domain ban: *.bad.example — wave"),
            ("second@bad.example", "Second", 0, "rtbl", "RTBL domain ban: *.bad.example — wave"),
        ]
    )
    assert bot.db.commit_count == 1
    assert "Domain ban *.bad.example" in bot.sent[0]["mbody"]
    assert "Also matched: 2 more occupant" in bot.sent[0]["mbody"]
    assert [event for event, _payload in bot.audit_events] == [
        "rtbl_ban_applied",
        "rtbl_ban_applied",
        "rtbl_ban_applied",
    ]
    assert all(payload["details"] == {"source_type": "domain", "source": "*.bad.example"} for _event, payload in bot.audit_events)
    assert len(bot.applied) == 6


@pytest.mark.asyncio
async def test_domain_locked_without_reason_and_without_announcement_uses_plain_comment(monkeypatch):
    import config

    monkeypatch.setattr(config, "ADMIN_ROOM", "admin@conference.example.test", raising=False)
    bot = LockedRtblMutationBot()
    bot.rtbl_announce = False
    bot.occupants = {"room@conference.example.test": {"Spam": {"jid": "spam@bad.example/res"}}}
    bot.protected_rooms = {"room@conference.example.test"}

    await bot._rtbl_apply_ban_domain_locked("*.bad.example", None)

    assert bot.sent == []
    assert bot.upserts == [("spam@bad.example", "Spam", 0, "rtbl", "RTBL domain ban: *.bad.example")]
    assert bot.audit_events[0][1]["comment"] == "RTBL domain ban: *.bad.example"


@pytest.mark.asyncio
async def test_domain_locked_only_protected_matches_announces_preview_and_skips_persistence(monkeypatch):
    import config

    monkeypatch.setattr(config, "ADMIN_ROOM", "admin@conference.example.test", raising=False)
    bot = LockedRtblMutationBot()
    bot.occupants = {
        "room@conference.example.test": {
            f"Admin{i}": {"jid": f"admin{i}@bad.example/res"} for i in range(7)
        }
    }
    bot.protected_jids = {f"admin{i}@bad.example" for i in range(7)}

    await bot._rtbl_apply_ban_domain_locked("bad.example", "wave")

    assert bot.upserts == []
    assert bot.db.execute_calls == []
    assert bot.db.commit_count == 0
    assert bot.applied == []
    assert "only protected admin/owner matches found" in bot.sent[0]["mbody"]
    assert "+2 more" in bot.sent[0]["mbody"]


@pytest.mark.asyncio
async def test_domain_locked_no_matches_and_no_protected_matches_is_silent(monkeypatch):
    import config

    monkeypatch.setattr(config, "ADMIN_ROOM", "admin@conference.example.test", raising=False)
    bot = LockedRtblMutationBot()
    bot.occupants = {"room@conference.example.test": {"Good": {"jid": "good@example.test/res"}}}

    await bot._rtbl_apply_ban_domain_locked("bad.example", "wave")

    assert bot.sent == []
    assert bot.upserts == []
    assert bot.db.execute_calls == []
    assert bot.applied == []


@pytest.mark.asyncio
async def test_rtbl_ban_is_still_covered_requires_concrete_bare_jid_and_hash_or_domain_cache():
    bot = LockedRtblMutationBot()

    assert await bot._rtbl_ban_is_still_covered(None) is False
    assert await bot._rtbl_ban_is_still_covered("") is False
    assert await bot._rtbl_ban_is_still_covered("*.bad.example") is False
    assert await bot._rtbl_ban_is_still_covered("not-a-jid") is False

    bot.rtbl_hash_cache[_rtbl_hash_jid("hash@example.test")] = "hash"
    assert await bot._rtbl_ban_is_still_covered("hash@example.test/res") is True

    bot.rtbl_hash_cache.clear()
    bot.rtbl_domain_cache["bad.example"] = "domain"
    assert await bot._rtbl_ban_is_still_covered("spam@sub.bad.example/res") is True
    assert await bot._rtbl_ban_is_still_covered("spam@good.example/res") is False


@pytest.mark.asyncio
async def test_cleanup_locked_removes_legacy_wildcards_and_uncovered_jids_but_keeps_covered(caplog):
    bot = LockedRtblMutationBot()
    bot.db.cleanup_rows = [
        (None,),
        ("*.legacy.example",),
        ("covered@example.test",),
        ("domain@bad.example",),
        ("gone@example.test",),
    ]
    bot.rtbl_hash_cache[_rtbl_hash_jid("covered@example.test")] = "hash"
    bot.rtbl_domain_cache["bad.example"] = "domain"

    with caplog.at_level("INFO", logger="banbot.rtbl_apply"):
        removed = await bot._rtbl_cleanup_stale_persisted_bans_locked("cleanup-test")

    assert removed == 2
    assert bot.unbanned == [("*.legacy.example", "cleanup-test"), ("gone@example.test", "cleanup-test")]
    assert "Removed 2 stale persisted RTBL ban" in caplog.text


@pytest.mark.asyncio
async def test_cleanup_locked_returns_zero_without_logging_when_everything_is_covered(caplog):
    bot = LockedRtblMutationBot()
    bot.db.cleanup_rows = [("covered@example.test",), ("domain@bad.example",)]
    bot.rtbl_hash_cache[_rtbl_hash_jid("covered@example.test")] = "hash"
    bot.rtbl_domain_cache["bad.example"] = "domain"

    with caplog.at_level("INFO", logger="banbot.rtbl_apply"):
        removed = await bot._rtbl_cleanup_stale_persisted_bans_locked()

    assert removed == 0
    assert bot.unbanned == []
    assert "Removed" not in caplog.text

@pytest.mark.asyncio
async def test_check_jid_against_rtbl_domain_cache_without_match_returns_false_and_does_not_apply():
    bot = RtblMutationBot()
    bot.rtbl_domain_cache = {
        "first.example": "first",
        "second.example": "second",
    }

    assert await bot.check_jid_against_rtbl("user@unlisted.example/res", "User") is False
    assert bot.jid_bans == []
    assert bot.domain_bans == []


@pytest.mark.asyncio
async def test_full_scan_skips_entries_whose_bare_jid_resolves_empty():
    bot = RtblMutationBot()
    bot.occupants = {
        "room@conference.example.test": {
            "Empty": {"jid": ""},
            "NoneValue": {"jid": None},
        }
    }
    bot.rtbl_hash_cache[_rtbl_hash_jid("empty@example.test")] = "hash"
    bot.rtbl_domain_cache["example.test"] = "domain"

    assert await bot._rtbl_check_all_occupants_against_caches("empty-bare") == (0, 0)
    assert bot.jid_bans == []
    assert bot.domain_bans == []


@pytest.mark.asyncio
async def test_hash_publish_scan_skips_entries_whose_bare_jid_resolves_empty():
    bot = RtblMutationBot()
    bot.occupants = {
        "room@conference.example.test": {
            "Empty": {"jid": ""},
            "NoneValue": {"jid": None},
        }
    }

    await bot._rtbl_check_all_occupants_for_hash(_rtbl_hash_jid("empty@example.test"), "hash")

    assert bot.jid_bans == []


@pytest.mark.asyncio
async def test_jid_locked_ignored_target_without_announcement_is_silent_and_stops(monkeypatch):
    import config

    monkeypatch.setattr(config, "ADMIN_ROOM", "admin@conference.example.test", raising=False)
    bot = LockedRtblMutationBot()
    bot.rtbl_announce = False
    bot.ignore_jids = {"bad@example.test"}

    await bot._rtbl_apply_ban_jid_locked("bad@example.test", "Bad", "listed")

    assert bot.sent == []
    assert bot.upserts == []
    assert bot.db.commit_count == 0
    assert bot.applied == []
    assert bot.audit_events == []


@pytest.mark.asyncio
async def test_jid_locked_protected_target_without_announcement_is_silent_and_stops(monkeypatch):
    import config

    monkeypatch.setattr(config, "ADMIN_ROOM", "admin@conference.example.test", raising=False)
    bot = LockedRtblMutationBot()
    bot.rtbl_announce = False
    bot.protected_jids = {"bad@example.test"}

    await bot._rtbl_apply_ban_jid_locked("bad@example.test", "Bad", "listed")

    assert bot.sent == []
    assert bot.upserts == []
    assert bot.db.commit_count == 0
    assert bot.applied == []
    assert bot.audit_events == []


class LockedRtblMutationBotWithoutDomainCacheRemoval(LockedRtblMutationBot):
    def __getattribute__(self, name):
        if name == "_remove_domain_bans_from_cache":
            raise AttributeError(name)
        return super().__getattribute__(name)


@pytest.mark.asyncio
async def test_domain_locked_persists_when_domain_cache_removal_hook_is_missing(monkeypatch):
    import config

    monkeypatch.setattr(config, "ADMIN_ROOM", "admin@conference.example.test", raising=False)
    bot = LockedRtblMutationBotWithoutDomainCacheRemoval()
    bot.rtbl_announce = False
    bot.occupants = {"room@conference.example.test": {"Spam": {"jid": "spam@bad.example/res"}}}
    bot.protected_rooms = {"room@conference.example.test"}

    await bot._rtbl_apply_ban_domain_locked("bad.example", "wave")

    assert bot.removed_domains == []
    assert bot.db.execute_calls == [
        (
            "DELETE FROM bans WHERE issuer = 'rtbl' AND target_type = 'domain' AND target = ?",
            ("bad.example",),
        )
    ]
    assert bot.upserts == [("spam@bad.example", "Spam", 0, "rtbl", "RTBL domain ban: *.bad.example — wave")]
    assert bot.db.commit_count == 1


@pytest.mark.asyncio
async def test_domain_locked_apply_failure_is_logged_per_room_without_rollback(monkeypatch, caplog):
    import config

    monkeypatch.setattr(config, "ADMIN_ROOM", "admin@conference.example.test", raising=False)
    bot = LockedRtblMutationBot()
    bot.rtbl_announce = False
    bot.occupants = {"room@conference.example.test": {"Spam": {"jid": "spam@bad.example/res"}}}
    bot.protected_rooms = {"room@conference.example.test", "second@conference.example.test"}
    bot.fail_apply_for = {"spam@bad.example"}

    with caplog.at_level("WARNING", logger="banbot.rtbl_apply"):
        await bot._rtbl_apply_ban_domain_locked("bad.example", "wave")

    assert bot.upserts == [("spam@bad.example", "Spam", 0, "rtbl", "RTBL domain ban: *.bad.example — wave")]
    assert bot.db.commit_count == 1
    assert bot.audit_events[0][0] == "rtbl_ban_applied"
    assert bot.applied == []
    assert caplog.text.count("Failed to apply domain-derived JID ban") == 2
