from __future__ import annotations

import pytest

from banbot.protections import ProtectionMixin
from banbot.protections.definitions import canonical_protection_name


class DummyProtections(ProtectionMixin):
    command_prefix = "!"
    list_page_size = 10

    def __init__(self) -> None:
        self.sent: list[tuple[str, str, str]] = []
        self.audit: list[tuple] = []
        self.protected_rooms = {"room@conference.example.org"}
        self.occupants = {}
        self.db = None
        self.init_protection_state()

    async def bot_send_message(self, *, mto: str, mbody: str, mtype: str = "groupchat") -> None:
        self.sent.append((mto, mbody, mtype))

    def _actor_jid_from_room_nick(self, room: str, nick: str) -> str:
        return f"{nick.lower()}@example.org"

    def is_admin_or_owner(self, room: str, nick: str | None = None, jid: str | None = None) -> bool:
        return False

    def is_bot_admin_or_owner(self, room: str) -> bool:
        return True

    async def persist_protection(self, name: str) -> None:
        self.persisted = name

    async def audit_event(self, *args, **kwargs) -> None:
        self.audit.append((args, kwargs))


def test_protection_aliases_resolve_common_names() -> None:
    assert canonical_protection_name("flood") == "FloodSpamProtection"
    assert canonical_protection_name("FirstMessageIsImageProtection") == "FirstMessageMediaProtection"
    assert canonical_protection_name("mention-limit") == "MentionLimitProtection"
    assert canonical_protection_name("media") == "FirstMessageMediaProtection"
    assert canonical_protection_name("policy") == "PolicyChangeNotification"


@pytest.mark.asyncio
async def test_protections_list_shows_enabled_disabled_icons() -> None:
    bot = DummyProtections()
    bot.protections["FloodSpamProtection"]["enabled"] = True

    await bot.cmd_protections_list("admin@conference.example.org", ["all"])

    body = bot.sent[-1][1]
    assert "🟢 (enabled) FloodSpamProtection [flood]" in body
    assert "🔴 (disabled) MentionLimitProtection [mentions]" in body


@pytest.mark.asyncio
async def test_protection_enable_and_config_set() -> None:
    bot = DummyProtections()

    await bot.cmd_protection_set_enabled(
        "admin@conference.example.org",
        "flood",
        True,
        "Admin",
    )
    await bot.cmd_protection_set_config(
        "admin@conference.example.org",
        "flood",
        "max_messages",
        "3",
        "Admin",
    )

    assert bot.protections["FloodSpamProtection"]["enabled"] is True
    assert bot.protections["FloodSpamProtection"]["max_messages"] == 3
    assert "FloodSpamProtection.max_messages updated" in bot.sent[-1][1]


@pytest.mark.asyncio
async def test_first_media_does_not_trigger_without_observed_join(fake_msg_factory) -> None:
    bot = DummyProtections()
    bot.protections["FirstMessageMediaProtection"]["enabled"] = True
    bot.occupants = {
        "room@conference.example.org": {
            "Spammer": {"jid": "spam@example.org", "role": "participant", "affiliation": "member"},
        }
    }
    msg = fake_msg_factory(
        room="room@conference.example.org",
        nick="Spammer",
        body="https://upload.example.org/spam.jpg",
    )

    handled = await bot.protections_on_message(
        msg,
        "room@conference.example.org",
        "Spammer",
        "https://upload.example.org/spam.jpg",
    )

    assert handled is False

@pytest.mark.asyncio
async def test_protection_reset_restores_defaults() -> None:
    bot = DummyProtections()
    bot.protections["FloodSpamProtection"]["enabled"] = True
    bot.protections["FloodSpamProtection"]["max_messages"] = 3

    await bot.cmd_protection_reset("admin@conference.example.org", "flood", "Admin")

    assert bot.protections["FloodSpamProtection"]["enabled"] is False
    assert bot.protections["FloodSpamProtection"]["max_messages"] == 10
    assert "reset to defaults" in bot.sent[-1][1]


@pytest.mark.asyncio
async def test_trusted_reporters_add_list_and_remove() -> None:
    bot = DummyProtections()

    await bot.cmd_trusted_reporters(
        "admin@conference.example.org",
        "add",
        ["Alice@Example.ORG/resource"],
        "Admin",
    )
    await bot.cmd_trusted_reporters_list("admin@conference.example.org", ["all"])

    assert bot.protections["TrustedReporters"]["reporters"] == ["alice@example.org"]
    assert "alice@" in bot.sent[-1][1]

    await bot.cmd_trusted_reporters(
        "admin@conference.example.org",
        "remove",
        ["alice@example.org"],
        "Admin",
    )

    assert bot.protections["TrustedReporters"]["reporters"] == []
    assert "removed" in bot.sent[-1][1]


@pytest.mark.asyncio
async def test_protections_dispatch_supports_reporter_shortcuts() -> None:
    bot = DummyProtections()

    await bot._dispatch_protections_command(
        "admin@conference.example.org",
        "Admin",
        ["reporters", "add", "bob@example.org"],
        "protections",
    )

    assert bot.protections["TrustedReporters"]["reporters"] == ["bob@example.org"]


@pytest.mark.asyncio
async def test_policy_alias_works_for_config_and_enable() -> None:
    bot = DummyProtections()

    await bot.cmd_protection_config("admin@conference.example.org", "policy")
    assert "PolicyChangeNotification config" in bot.sent[-1][1]

    await bot.cmd_protection_set_enabled(
        "admin@conference.example.org",
        "policy",
        False,
        "Admin",
    )
    assert bot.protections["PolicyChangeNotification"]["enabled"] is False
    assert "PolicyChangeNotification disabled" in bot.sent[-1][1]


@pytest.mark.asyncio
async def test_mention_limit_counts_prefixed_display_nicks(fake_msg_factory) -> None:
    bot = DummyProtections()
    bot.protections["MentionLimitProtection"]["enabled"] = True
    bot.protections["MentionLimitProtection"]["max_mentions"] = 2
    bot.protections["MentionLimitProtection"]["action"] = "notify"
    bot.occupants = {
        "room@conference.example.org": {
            "Spammer": {"jid": "spam@example.org", "role": "participant", "affiliation": "member"},
            "creme": {"jid": "creme@example.org", "role": "participant", "affiliation": "member"},
            "adminbot_dev": {"jid": "bot@example.org", "role": "participant", "affiliation": "member"},
            "fab": {"jid": "fab@example.org", "role": "participant", "affiliation": "member"},
        }
    }
    msg = fake_msg_factory(
        room="room@conference.example.org",
        nick="Spammer",
        body="hi ~creme adminbot_dev fab",
    )

    handled = await bot.protections_on_message(
        msg,
        "room@conference.example.org",
        "Spammer",
        "hi ~creme adminbot_dev fab",
    )

    assert handled is True
    assert "MentionLimitProtection triggered" in bot.sent[-1][1]


@pytest.mark.asyncio
async def test_mention_limit_uses_xep0045_roster_cache(fake_msg_factory) -> None:
    class DummyMucPlugin:
        rooms = {
            "room@conference.example.org": {
                "creme": {},
                "adminbot_dev": {},
                "fab": {},
            }
        }

    bot = DummyProtections()
    bot.protections["MentionLimitProtection"]["enabled"] = True
    bot.protections["MentionLimitProtection"]["max_mentions"] = 2
    bot.protections["MentionLimitProtection"]["action"] = "notify"
    bot.plugin = {"xep_0045": DummyMucPlugin()}
    bot.occupants = {
        "room@conference.example.org": {
            "Spammer": {"jid": "spam@example.org", "role": "participant", "affiliation": "member"},
        }
    }
    msg = fake_msg_factory(
        room="room@conference.example.org",
        nick="Spammer",
        body="hi ~creme adminbot_dev fab",
    )

    handled = await bot.protections_on_message(
        msg,
        "room@conference.example.org",
        "Spammer",
        "hi ~creme adminbot_dev fab",
    )

    assert handled is True
    assert "MentionLimitProtection triggered" in bot.sent[-1][1]
