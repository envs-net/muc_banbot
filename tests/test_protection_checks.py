from __future__ import annotations

import time

import pytest

from banbot.protections import ProtectionMixin


ROOM = "room@conference.example.org"
ADMIN_ROOM = "admin@conference.example.org"


class AsyncNullContext:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class DummyMucPlugin:
    def __init__(self) -> None:
        self.room_configs: list[tuple[str, dict]] = []
        self.roles: list[tuple[str, str, str, str | None]] = []
        self.rooms = {ROOM: {"Alice": {}, "Bob": {}, "Carol": {}, "Spammer": {}}}

    async def set_room_config(self, room: str, config: dict) -> None:
        self.room_configs.append((room, dict(config)))

    async def set_role(self, *, room: str, nick: str, role: str, reason: str | None = None) -> None:
        self.roles.append((room, nick, role, reason))


class DummyProtections(ProtectionMixin):
    command_prefix = "!"
    list_page_size = 10

    def __init__(self) -> None:
        self.sent: list[tuple[str, str, str]] = []
        self.audit: list[tuple[tuple, dict]] = []
        self.bans: list[tuple[str, int | None, str, str | None]] = []
        self.redactions: list[tuple[object, str, str | None]] = []
        self.protected_rooms = {ROOM}
        self.admin_nicks: set[str] = set()
        self.bot_is_admin = True
        self.occupants = {
            ROOM: {
                "Alice": {"jid": "alice@example.org", "role": "participant", "affiliation": "member"},
                "Bob": {"jid": "bob@example.org", "role": "participant", "affiliation": "member"},
                "Carol": {"jid": "carol@example.org", "role": "participant", "affiliation": "member"},
                "Spammer": {"jid": "spam@example.org", "role": "participant", "affiliation": "member"},
                "Admin": {"jid": "admin@example.org", "role": "moderator", "affiliation": "owner"},
            }
        }
        self.db = None
        self.muc_plugin = DummyMucPlugin()
        self.plugin = {"xep_0045": self.muc_plugin}
        self.muc_write_semaphore = AsyncNullContext()
        self.redaction_enabled = True
        self.init_protection_state()

    async def bot_send_message(self, *, mto: str, mbody: str, mtype: str = "groupchat") -> None:
        self.sent.append((mto, mbody, mtype))

    def _actor_jid_from_room_nick(self, room: str, nick: str) -> str:
        return self.occupants.get(room, {}).get(nick, {}).get("jid") or f"{nick.lower()}@example.org"

    def is_admin_or_owner(self, room: str, nick: str | None = None, jid: str | None = None) -> bool:
        return bool(nick and nick in self.admin_nicks)

    def is_bot_admin_or_owner(self, room: str) -> bool:
        return self.bot_is_admin

    async def persist_protection(self, name: str) -> None:
        pass

    async def audit_event(self, *args, **kwargs) -> None:
        self.audit.append((args, kwargs))

    async def ban_all(self, target, until, issuer, comment=None):
        self.bans.append((target, until, issuer, comment))

    async def _protection_redact_message(self, msg, reason: str, actor: str | None) -> None:
        self.redactions.append((msg, reason, actor))


def last_body(bot: DummyProtections) -> str:
    return bot.sent[-1][1]


@pytest.mark.asyncio
async def test_message_protections_ignore_unprotected_rooms(fake_msg_factory) -> None:
    bot = DummyProtections()
    bot.protections["FloodSpamProtection"].update({"enabled": True, "max_messages": 1, "action": "ban"})
    msg = fake_msg_factory(room="other@conference.example.org", nick="Spammer", body="spam")

    handled = await bot.protections_on_message(msg, "other@conference.example.org", "Spammer", "spam")

    assert handled is False
    assert bot.bans == []


@pytest.mark.asyncio
async def test_message_protections_ignore_admin_or_owner(fake_msg_factory) -> None:
    bot = DummyProtections()
    bot.admin_nicks.add("Admin")
    bot.protections["FloodSpamProtection"].update({"enabled": True, "max_messages": 1, "action": "ban"})
    msg = fake_msg_factory(room=ROOM, nick="Admin", body="spam")

    handled = await bot.protections_on_message(msg, ROOM, "Admin", "spam")

    assert handled is False
    assert bot.bans == []


@pytest.mark.asyncio
async def test_flood_triggers_only_after_threshold_and_clears_window(fake_msg_factory) -> None:
    bot = DummyProtections()
    bot.protections["FloodSpamProtection"].update({"enabled": True, "max_messages": 2, "window_seconds": 60, "action": "ban"})
    msg = fake_msg_factory(room=ROOM, nick="Spammer", body="spam")

    assert await bot.protections_on_message(msg, ROOM, "Spammer", "spam") is False
    assert await bot.protections_on_message(msg, ROOM, "Spammer", "spam") is False
    assert await bot.protections_on_message(msg, ROOM, "Spammer", "spam") is True

    assert bot.bans == [("spam@example.org", None, "protection:FloodSpamProtection", "spam/flood detected")]
    key = ("FloodSpamProtection", ROOM, "spam@example.org")
    assert list(bot.protection_message_windows[key]) == []


@pytest.mark.asyncio
async def test_flood_window_expires_old_hits(fake_msg_factory, monkeypatch) -> None:
    bot = DummyProtections()
    bot.protections["FloodSpamProtection"].update({"enabled": True, "max_messages": 1, "window_seconds": 10, "action": "ban"})
    msg = fake_msg_factory(room=ROOM, nick="Spammer", body="spam")
    times = iter([100.0, 111.0])
    monkeypatch.setattr("banbot.protections.checks.time.time", lambda: next(times))

    assert await bot.protections_on_message(msg, ROOM, "Spammer", "spam") is False
    assert await bot.protections_on_message(msg, ROOM, "Spammer", "spam") is False
    assert bot.bans == []


@pytest.mark.asyncio
async def test_first_media_triggers_only_for_observed_recent_first_message(fake_msg_factory) -> None:
    bot = DummyProtections()
    bot.protections["FirstMessageMediaProtection"].update({"enabled": True, "action": "tempban", "redact": True})
    await bot.protection_on_join(ROOM, "Spammer", "spam@example.org/resource")
    msg = fake_msg_factory(room=ROOM, nick="Spammer", body="https://upload.example.org/file.jpg")

    handled = await bot.protections_on_message(msg, ROOM, "Spammer", "https://upload.example.org/file.jpg")

    assert handled is True
    assert bot.bans[0][0] == "spam@example.org"
    assert bot.bans[0][1] is not None
    assert bot.bans[0][2] == "protection:FirstMessageMediaProtection"
    assert bot.redactions and bot.redactions[0][1] == "first message was media spam"


@pytest.mark.asyncio
async def test_first_media_does_not_trigger_on_second_message(fake_msg_factory) -> None:
    bot = DummyProtections()
    bot.protections["FirstMessageMediaProtection"].update({"enabled": True, "action": "ban"})
    await bot.protection_on_join(ROOM, "Spammer", "spam@example.org/resource")
    first = fake_msg_factory(room=ROOM, nick="Spammer", body="hello")
    second = fake_msg_factory(room=ROOM, nick="Spammer", body="https://upload.example.org/file.jpg")

    assert await bot.protections_on_message(first, ROOM, "Spammer", "hello") is False
    assert await bot.protections_on_message(second, ROOM, "Spammer", "https://upload.example.org/file.jpg") is False
    assert bot.bans == []


@pytest.mark.asyncio
async def test_first_media_respects_join_grace(fake_msg_factory, monkeypatch) -> None:
    bot = DummyProtections()
    bot.protections["FirstMessageMediaProtection"].update({"enabled": True, "join_grace_seconds": 5, "action": "ban"})
    monkeypatch.setattr("banbot.protections.checks.time.time", lambda: 100.0)
    await bot.protection_on_join(ROOM, "Spammer", "spam@example.org")
    monkeypatch.setattr("banbot.protections.checks.time.time", lambda: 106.0)
    msg = fake_msg_factory(room=ROOM, nick="Spammer", body="https://upload.example.org/file.jpg")

    handled = await bot.protections_on_message(msg, ROOM, "Spammer", "https://upload.example.org/file.jpg")

    assert handled is False
    assert bot.bans == []


@pytest.mark.asyncio
async def test_mention_limit_triggers_on_more_than_limit(fake_msg_factory) -> None:
    bot = DummyProtections()
    bot.protections["MentionLimitProtection"].update({"enabled": True, "max_mentions": 2, "action": "notify"})
    msg = fake_msg_factory(room=ROOM, nick="Spammer", body="hi @Alice ~Bob Carol")

    handled = await bot.protections_on_message(msg, ROOM, "Spammer", "hi @Alice ~Bob Carol")

    assert handled is True
    assert "MentionLimitProtection triggered" in last_body(bot)
    assert "Action: notify" in last_body(bot)


@pytest.mark.asyncio
async def test_mention_limit_equal_limit_does_not_trigger(fake_msg_factory) -> None:
    bot = DummyProtections()
    bot.protections["MentionLimitProtection"].update({"enabled": True, "max_mentions": 2, "action": "notify"})
    msg = fake_msg_factory(room=ROOM, nick="Spammer", body="hi Alice Bob")

    handled = await bot.protections_on_message(msg, ROOM, "Spammer", "hi Alice Bob")

    assert handled is False
    assert bot.sent == []


@pytest.mark.asyncio
async def test_wordlist_new_joiner_triggers_case_insensitive(fake_msg_factory) -> None:
    bot = DummyProtections()
    bot.protections["WordListNewJoinerProtection"].update({"enabled": True, "words": ["spamword"], "action": "kick"})
    await bot.protection_on_join(ROOM, "Spammer", "spam@example.org")
    msg = fake_msg_factory(room=ROOM, nick="Spammer", body="This has SPAMWORD inside")

    handled = await bot.protections_on_message(msg, ROOM, "Spammer", "This has SPAMWORD inside")

    assert handled is True
    assert bot.muc_plugin.roles == [(ROOM, "Spammer", "none", "blocked word from new joiner: spamword")]
    assert bot.redactions and bot.redactions[0][1] == "blocked word from new joiner: spamword"


@pytest.mark.asyncio
async def test_wordlist_does_not_trigger_without_observed_join(fake_msg_factory) -> None:
    bot = DummyProtections()
    bot.protections["WordListNewJoinerProtection"].update({"enabled": True, "words": ["spamword"], "action": "ban"})
    msg = fake_msg_factory(room=ROOM, nick="Spammer", body="spamword")

    handled = await bot.protections_on_message(msg, ROOM, "Spammer", "spamword")

    assert handled is False
    assert bot.bans == []


@pytest.mark.asyncio
async def test_join_wave_locks_room_and_notifies_once_during_lockdown() -> None:
    bot = DummyProtections()
    bot.protections["JoinWaveShortCircuitProtection"].update({"enabled": True, "max_joins": 1, "window_seconds": 60, "lockdown_seconds": 900})

    await bot.protection_on_join(ROOM, "Alice", "alice@example.org")
    await bot.protection_on_join(ROOM, "Bob", "bob@example.org")
    await bot.protection_on_join(ROOM, "Carol", "carol@example.org")

    assert bot.muc_plugin.room_configs == [
        (ROOM, {"muc#roomconfig_membersonly": "1", "muc#roomconfig_moderatedroom": "1"})
    ]
    assert len([body for _, body, _ in bot.sent if "JoinWaveShortCircuitProtection triggered" in body]) == 1


@pytest.mark.asyncio
async def test_join_wave_notify_only_skips_room_config() -> None:
    bot = DummyProtections()
    bot.protections["JoinWaveShortCircuitProtection"].update({"enabled": True, "max_joins": 1, "notify_only": True})

    await bot.protection_on_join(ROOM, "Alice", "alice@example.org")
    await bot.protection_on_join(ROOM, "Bob", "bob@example.org")

    assert bot.muc_plugin.room_configs == []
    assert "Action: notify only" in last_body(bot)


@pytest.mark.asyncio
async def test_notify_policy_change_respects_enabled_and_event_toggles() -> None:
    bot = DummyProtections()

    await bot.notify_policy_change("ban_created", actor="admin@example.org", target="spam@example.org", room=ROOM, comment="spam")
    assert "Policy change" in last_body(bot)
    assert "Target: spam@" in last_body(bot)
    assert "example.org" in last_body(bot)

    bot.sent.clear()
    bot.protections["PolicyChangeNotification"]["notify_bans"] = False
    await bot.notify_policy_change("ban_created", actor="admin@example.org", target="spam@example.org")
    assert bot.sent == []

    bot.protections["PolicyChangeNotification"]["notify_unbans"] = False
    await bot.notify_policy_change("unban", actor="admin@example.org", target="spam@example.org")
    assert bot.sent == []

    bot.protections["PolicyChangeNotification"]["enabled"] = False
    await bot.notify_policy_change("config", actor="admin@example.org", target="spam@example.org")
    assert bot.sent == []


def test_public_protection_api_stays_available() -> None:
    bot = DummyProtections()
    for name in [
        "init_protection_state",
        "protection_on_join",
        "protections_on_message",
        "cmd_protection_set_enabled",
        "cmd_protections_list",
        "notify_policy_change",
    ]:
        assert callable(getattr(bot, name))


@pytest.mark.asyncio
async def test_message_protections_stop_after_first_trigger(fake_msg_factory) -> None:
    bot = DummyProtections()
    bot.protections["FloodSpamProtection"].update({"enabled": True, "max_messages": 1, "action": "ban"})
    bot.protections["MentionLimitProtection"].update({"enabled": True, "max_mentions": 1, "action": "ban"})
    first = fake_msg_factory(room=ROOM, nick="Spammer", body="hello")
    second = fake_msg_factory(room=ROOM, nick="Spammer", body="hi Alice Bob")

    assert await bot.protections_on_message(first, ROOM, "Spammer", "hello") is False
    handled = await bot.protections_on_message(second, ROOM, "Spammer", "hi Alice Bob")

    assert handled is True
    assert bot.bans == [("spam@example.org", None, "protection:FloodSpamProtection", "spam/flood detected")]
