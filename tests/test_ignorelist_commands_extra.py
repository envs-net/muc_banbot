"""Additional ignorelist command and cleanup tests."""

from __future__ import annotations

import logging

import pytest

aiosqlite = pytest.importorskip("aiosqlite")

from banbot.cache import CacheMixin
from banbot.db import DatabaseMixin
from banbot.ignorelist import IgnorelistMixin
from banbot.utils import bare_jid


class IgnoreCommandBot(IgnorelistMixin, DatabaseMixin, CacheMixin):
    def __init__(self):
        self.sent = []
        self.unbans = []
        self.events = []
        self.audit_events = []
        self.command_prefix = "!"
        self.ban_cache = {}
        self.ban_index_by_jid = {}
        self.ban_index_by_nick = {}
        self.ban_index_by_domain = {}

    def bare_jid(self, jid):
        return bare_jid(jid)

    async def bot_send_message(self, **kwargs):
        self.sent.append(kwargs)

    async def unban_all(self, target, issuer="ignorelist"):
        self.unbans.append((target, issuer))
        await self.delete_ban_db(target)
        await self.load_bans_from_db()

    def log_event(self, level, event, **fields):
        self.events.append((level, event, fields))

    async def audit_event(self, event_type, **kwargs):
        self.audit_events.append((event_type, kwargs))


async def make_bot():
    bot = IgnoreCommandBot()
    await bot.setup_db()
    await bot.setup_ignorelist()
    await bot.load_bans_from_db()
    return bot


@pytest.mark.asyncio
async def test_ignore_add_jid_updates_cache_audit_and_unbans_existing_ban(temp_db_path):
    bot = await make_bot()
    try:
        await bot.upsert_ban_db("user@example.org", "Nick", 0, "admin", "old ban")
        await bot.load_bans_from_db()

        await bot.cmd_ignore(
            ["add", "User@Example.org", "trusted", "operator"],
            "admin@conference.example.test",
            actor="admin@example.test",
        )

        assert bot.is_ignored_jid("user@example.org/resource")
        assert bot.unbans == [("user@example.org", "ignorelist")]
        assert bot.audit_events[-1][0] == "ignorelist_added"
        assert bot.audit_events[-1][1]["target"] == "user@example.org"
        assert "Added user@example.org" in bot.sent[-1]["mbody"]
    finally:
        await bot.db.close()


@pytest.mark.asyncio
async def test_ignore_add_domain_unbans_exact_domain_and_matching_rtbl_jids(temp_db_path):
    bot = await make_bot()
    try:
        await bot.upsert_ban_db("*.example.org", None, 0, "rtbl", "legacy domain")
        await bot.upsert_ban_db("bad@example.org", "Bad", 0, "rtbl", "domain match")
        await bot.upsert_ban_db("other@elsewhere.org", "Other", 0, "rtbl", "other")
        await bot.load_bans_from_db()

        await bot.cmd_ignore(
            ["add", "*.example.org", "trusted", "domain"],
            "admin@conference.example.test",
            actor="admin@example.test",
            command_name="whitelist",
        )

        assert bot.is_ignored_domain("sub.example.org")
        assert ("*.example.org", "ignorelist") in bot.unbans
        assert ("bad@example.org", "ignorelist") in bot.unbans
        assert ("other@elsewhere.org", "ignorelist") not in bot.unbans
        assert "Whitelist: Added *.example.org" in bot.sent[-1]["mbody"]
    finally:
        await bot.db.close()


@pytest.mark.asyncio
async def test_ignore_list_paginates_and_remove_accepts_non_wildcard_domain(temp_db_path):
    bot = await make_bot()
    try:
        for idx in range(12):
            await bot.cmd_ignore(
                ["add", f"user{idx}@example.org", "reason"],
                "admin@conference.example.test",
                actor="admin@example.test",
            )
        await bot.cmd_ignore(["list", "last"], "admin@conference.example.test")
        assert "Ignorelist (12) - Page 2/2" in bot.sent[-1]["mbody"]

        await bot.cmd_ignore(["add", "*.example.net"], "admin@conference.example.test")
        await bot.cmd_ignore(["remove", "example.net"], "admin@conference.example.test")

        assert not bot.is_ignored_domain("example.net")
        assert "Removed *.example.net" in bot.sent[-1]["mbody"]
    finally:
        await bot.db.close()


@pytest.mark.asyncio
async def test_ignore_invalid_and_unknown_subcommands_report_help(temp_db_path):
    bot = await make_bot()
    try:
        await bot.cmd_ignore(["add", "not a jid"], "admin@conference.example.test")
        assert "Invalid domain" in bot.sent[-1]["mbody"]

        await bot.cmd_ignore(["remove"], "admin@conference.example.test")
        assert "Usage: !ignore remove" in bot.sent[-1]["mbody"]

        await bot.cmd_ignore(["wat"], "admin@conference.example.test")
        assert "Unknown sub-command" in bot.sent[-1]["mbody"]
    finally:
        await bot.db.close()
