from __future__ import annotations

import time

import pytest

from banbot.protections import ProtectionMixin


ROOM = "room@conference.example.org"
ADMIN_ROOM = "admin@conference.example.org"


class DummyProtections(ProtectionMixin):
    command_prefix = "!"
    list_page_size = 2

    def __init__(self) -> None:
        self.sent: list[tuple[str, str, str]] = []  # (recipient, body, message type)
        self.persisted: list[str] = []
        self.audit: list[tuple[tuple, dict]] = []
        self.bans: list[tuple[str, int | None, str, str | None]] = []
        self.protected_rooms = {ROOM}
        self.admin_nicks: set[str] = {"Admin"}
        self.occupants = {
            ROOM: {
                "Admin": {"jid": "admin@example.org", "role": "moderator", "affiliation": "owner"},
                "Alice": {"jid": "alice@example.org", "role": "participant", "affiliation": "member"},
                "Bob": {"jid": "bob@example.org", "role": "participant", "affiliation": "member"},
                "Spammer": {"jid": "spam@example.org", "role": "participant", "affiliation": "member"},
            }
        }
        self.db = None
        self.init_protection_state()

    async def bot_send_message(self, *, mto: str, mbody: str, mtype: str = "groupchat") -> None:
        self.sent.append((mto, mbody, mtype))

    def _actor_jid_from_room_nick(self, room: str, nick: str) -> str:
        info = self.occupants.get(room, {}).get(nick, {})
        return info.get("jid") or f"{nick.lower()}@example.org"

    def is_admin_or_owner(self, room: str, nick: str | None = None, jid: str | None = None) -> bool:
        if nick is not None and nick in self.admin_nicks:
            return True

        if jid is not None:
            for info in self.occupants.get(room, {}).values():
                if info.get("jid") == jid and info.get("affiliation") in {"owner", "admin"}:
                    return True

        return False

    def is_bot_admin_or_owner(self, room: str) -> bool:
        return True

    async def persist_protection(self, name: str) -> None:
        self.persisted.append(name)

    async def audit_event(self, *args, **kwargs) -> None:
        self.audit.append((args, kwargs))

    async def ban_all(self, target, until, issuer, comment=None, *, auto_redact=True):
        self.bans.append((target, until, issuer, comment))


def last_body(bot: DummyProtections) -> str:
    return bot.sent[-1][1]


@pytest.mark.asyncio
async def test_dispatch_without_args_lists_protections() -> None:
    bot = DummyProtections()

    await bot._dispatch_protections_command(ADMIN_ROOM, "Admin", [], "protections")

    assert "🛡️ Protections (8) - Page 1/4:" in last_body(bot)
    assert "FloodSpamProtection [flood]" in last_body(bot)
    assert "SimilarMessageProtection [similar]" in last_body(bot)


@pytest.mark.asyncio
async def test_list_supports_last_page_and_alias_display() -> None:
    bot = DummyProtections()

    await bot.cmd_protections_list(ADMIN_ROOM, ["last"])

    body = last_body(bot)
    assert "Page 4/4" in body
    assert "PolicyChangeNotification [policy]" in body


@pytest.mark.asyncio
async def test_enable_disable_accept_displayed_aliases() -> None:
    bot = DummyProtections()

    await bot._dispatch_protections_command(ADMIN_ROOM, "Admin", ["enable", "media"], "protection")
    await bot._dispatch_protections_command(ADMIN_ROOM, "Admin", ["disable", "media"], "protection")

    assert bot.protections["FirstMessageMediaProtection"]["enabled"] is False
    assert bot.persisted == ["FirstMessageMediaProtection", "FirstMessageMediaProtection"]
    assert "FirstMessageMediaProtection disabled" in last_body(bot)


@pytest.mark.asyncio
async def test_shorthand_show_set_and_reset_use_same_alias_resolver() -> None:
    bot = DummyProtections()

    await bot._dispatch_protections_command(ADMIN_ROOM, "Admin", ["policy", "show"], "protections")
    assert "PolicyChangeNotification config" in last_body(bot)

    await bot._dispatch_protections_command(ADMIN_ROOM, "Admin", ["policy", "set", "notify_config", "false"], "protections")
    assert bot.protections["PolicyChangeNotification"]["notify_config"] is False
    assert "PolicyChangeNotification.notify_config updated" in last_body(bot)

    await bot._dispatch_protections_command(ADMIN_ROOM, "Admin", ["policy", "reset"], "protections")
    assert bot.protections["PolicyChangeNotification"]["notify_config"] is True
    assert "reset to defaults" in last_body(bot)


@pytest.mark.asyncio
async def test_set_rejects_unknown_keys_invalid_actions_and_bad_bool_values() -> None:
    bot = DummyProtections()

    await bot.cmd_protection_set_config(ADMIN_ROOM, "flood", "missing", "1", "Admin")
    assert "Unknown config key" in last_body(bot)

    await bot.cmd_protection_set_config(ADMIN_ROOM, "flood", "action", "explode", "Admin")
    assert "action must be one of" in last_body(bot)

    await bot.cmd_protection_set_config(ADMIN_ROOM, "policy", "notify_config", "maybe", "Admin")
    assert "notify_config must be True or False" in last_body(bot)


@pytest.mark.asyncio
async def test_duration_values_are_parsed_for_time_config() -> None:
    bot = DummyProtections()

    await bot.cmd_protection_set_config(ADMIN_ROOM, "flood", "window_seconds", "2m", "Admin")

    assert bot.protections["FloodSpamProtection"]["window_seconds"] == 120
    assert "FloodSpamProtection.window_seconds updated" in last_body(bot)


@pytest.mark.asyncio
async def test_similar_message_alias_and_similarity_percent_validation() -> None:
    bot = DummyProtections()

    await bot.cmd_protection_set_config(ADMIN_ROOM, "similar", "similarity_percent", "85", "Admin")
    assert bot.protections["SimilarMessageProtection"]["similarity_percent"] == 85
    assert "SimilarMessageProtection.similarity_percent updated" in last_body(bot)

    await bot.cmd_protection_set_config(ADMIN_ROOM, "similar", "similarity_percent", "101", "Admin")
    assert "similarity_percent must be an integer from 1 to 100" in last_body(bot)


@pytest.mark.asyncio
async def test_trusted_reporters_manage_jids_without_json() -> None:
    bot = DummyProtections()

    await bot.cmd_trusted_reporters(ADMIN_ROOM, "add", ["Bob@Example.ORG/resource"], "Admin")
    await bot.cmd_trusted_reporters(ADMIN_ROOM, "add", ["bob@example.org"], "Admin")
    assert bot.protections["TrustedReporters"]["reporters"] == ["bob@example.org"]
    assert "already exists" in last_body(bot)

    await bot.cmd_trusted_reporters(ADMIN_ROOM, "rm", ["bob@example.org"], "Admin")
    assert bot.protections["TrustedReporters"]["reporters"] == []
    assert "removed" in last_body(bot)


@pytest.mark.asyncio
async def test_trusted_reporters_list_paginates() -> None:
    bot = DummyProtections()
    bot.protections["TrustedReporters"]["reporters"] = [
        "a@example.org",
        "b@example.org",
        "c@example.org",
    ]

    await bot.cmd_trusted_reporters_list(ADMIN_ROOM, ["last"])

    body = last_body(bot)
    assert "Trusted reporters (3) - Page 2/2" in body
    assert "c@" in body


@pytest.mark.asyncio
async def test_report_command_is_noop_when_disabled() -> None:
    bot = DummyProtections()

    await bot.cmd_protection_report(ROOM, "Alice", ["Spammer", "spam"])

    assert bot.sent == []
    assert bot.bans == []


@pytest.mark.asyncio
async def test_report_rejects_untrusted_reporter() -> None:
    bot = DummyProtections()
    bot.protections["TrustedReporters"].update({"enabled": True, "reporters": ["bob@example.org"]})

    await bot.cmd_protection_report(ROOM, "Alice", ["Spammer", "spam"])

    assert "not a trusted reporter" in last_body(bot)
    assert bot.bans == []


@pytest.mark.asyncio
async def test_report_threshold_ignores_duplicate_reporter_and_then_triggers_action() -> None:
    bot = DummyProtections()
    bot.protections["TrustedReporters"].update({
        "enabled": True,
        "reporters": ["alice@example.org", "bob@example.org"],
        "threshold": 2,
        "action": "tempban",
        "tempban_seconds": 60,
    })

    await bot.cmd_protection_report(ROOM, "Alice", ["Spammer", "spam"])
    await bot.cmd_protection_report(ROOM, "Alice", ["Spammer", "spam again"])
    assert bot.bans == []
    assert "(1/2)" in last_body(bot)

    before = int(time.time())
    await bot.cmd_protection_report(ROOM, "Bob", ["Spammer", "spam"])

    assert len(bot.bans) == 1
    target, until, issuer, comment = bot.bans[0]
    assert target == "spam@example.org"
    assert until is not None and until >= before
    assert issuer == "protection:TrustedReporters"
    assert comment == "spam"
    assert (ROOM, "Spammer") not in bot.protection_trusted_reports


@pytest.mark.asyncio
async def test_report_does_not_act_on_admin_or_owner_target() -> None:
    bot = DummyProtections()
    bot.protections["TrustedReporters"].update({
        "enabled": True,
        "reporters": ["alice@example.org"],
        "threshold": 1,
        "action": "tempban",
    })

    await bot.cmd_protection_report(ROOM, "Alice", ["Admin", "spam"])

    assert bot.bans == []
    assert "exempt" in last_body(bot).lower()


@pytest.mark.asyncio
async def test_joinwave_action_accepts_notify_and_rejects_other_actions() -> None:
    bot = DummyProtections()

    await bot.cmd_protection_set_config(ADMIN_ROOM, "joinwave", "action", "notify", "Admin")
    assert bot.protections["JoinWaveShortCircuitProtection"]["action"] == "notify"
    assert "updated" in last_body(bot)

    await bot.cmd_protection_set_config(ADMIN_ROOM, "joinwave", "action", "ban", "Admin")
    assert "action must be one of: lockdown, notify" in last_body(bot)


@pytest.mark.asyncio
async def test_set_same_config_value_is_noop() -> None:
    bot = DummyProtections()
    bot.protections["JoinWaveShortCircuitProtection"]["max_joins"] = 3

    await bot.cmd_protection_set_config(ADMIN_ROOM, "joinwave", "max_joins", "3", "Admin")

    assert bot.protections["JoinWaveShortCircuitProtection"]["max_joins"] == 3
    assert bot.persisted == []
    assert bot.audit == []
    assert "is already 3" in last_body(bot)


@pytest.mark.asyncio
async def test_enable_same_state_is_noop() -> None:
    bot = DummyProtections()
    bot.protections["FloodSpamProtection"]["enabled"] = True

    await bot.cmd_protection_set_enabled(ADMIN_ROOM, "flood", True, "Admin")

    assert bot.persisted == []
    assert bot.audit == []
    assert "already enabled" in last_body(bot)
