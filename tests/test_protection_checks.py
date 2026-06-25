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


class DummyRoomConfigForm:
    def __init__(self) -> None:
        self.type = "form"
        self.values: dict[str, str] = {"muc#roomconfig_persistentroom": "1"}

    def set_type(self, value: str) -> None:
        self.type = value

    def set_values(self, values: dict[str, str]) -> None:
        self.values.update(values)


class DummyMucPlugin:
    def __init__(self) -> None:
        self.room_configs: list[tuple[str, dict, str]] = []
        self.roles: list[tuple[str, str, str, str | None]] = []
        self.rooms = {ROOM: {"Alice": {}, "Bob": {}, "Carol": {}, "Spammer": {}}}

    async def get_room_config(self, room: str) -> DummyRoomConfigForm:
        return DummyRoomConfigForm()

    async def set_room_config(self, room: str, config: DummyRoomConfigForm) -> None:
        self.room_configs.append((room, dict(config.values), config.type))

    async def set_role(self, *, room: str, nick: str, role: str, reason: str | None = None) -> None:
        self.roles.append((room, nick, role, reason))


class DummyProtections(ProtectionMixin):
    command_prefix = "!"
    list_page_size = 10

    def __init__(self) -> None:
        self.sent: list[tuple[str, str, str]] = []
        self.audit: list[tuple[tuple, dict]] = []
        self.bans: list[tuple[str, int | None, str, str | None]] = []
        self.ban_auto_redact_flags: list[bool] = []
        self.redactions: list[tuple[object, str, str | None]] = []
        self.target_redactions: list[tuple[str, str | None, str | None, bool, str]] = []
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

    async def ban_all(self, target, until, issuer, comment=None, *, auto_redact=True):
        self.bans.append((target, until, issuer, comment))
        self.ban_auto_redact_flags.append(auto_redact)

    async def _protection_redact_message(self, msg, reason: str, actor: str | None) -> None:
        self.redactions.append((msg, reason, actor))

    async def redact_jid_messages(
        self,
        jid: str,
        reason: str | None = None,
        actor: str | None = None,
        announce: bool = True,
        title: str = "Redaction completed",
    ) -> dict[str, object]:
        self.target_redactions.append((jid, reason, actor, announce, title))
        await self.bot_send_message(
            mto=ADMIN_ROOM,
            mbody=f"{title}\nTarget: {jid}\nReason: {reason}",
            mtype="groupchat",
        )
        return {"found": 1, "redacted": 1, "failed": 0, "skipped": 0}


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

    await bot.protection_on_join(ROOM, "Joiner1", "joiner1@example.org")
    await bot.protection_on_join(ROOM, "Joiner2", "joiner2@example.org")
    await bot.protection_on_join(ROOM, "Joiner3", "joiner3@example.org")

    assert bot.muc_plugin.room_configs == [
        (
            ROOM,
            {
                "muc#roomconfig_persistentroom": "1",
                "muc#roomconfig_membersonly": "1",
                "muc#roomconfig_moderatedroom": "1",
            },
            "submit",
        )
    ]
    assert len([body for _, body, _ in bot.sent if "JoinWaveShortCircuitProtection triggered" in body]) == 1


@pytest.mark.asyncio
async def test_join_wave_notify_only_skips_room_config() -> None:
    bot = DummyProtections()
    bot.protections["JoinWaveShortCircuitProtection"].update({"enabled": True, "max_joins": 1, "notify_only": True})

    await bot.protection_on_join(ROOM, "Joiner1", "joiner1@example.org")
    await bot.protection_on_join(ROOM, "Joiner2", "joiner2@example.org")

    assert bot.muc_plugin.room_configs == []

    assert "JoinWaveShortCircuitProtection triggered" in last_body(bot)


@pytest.mark.asyncio
async def test_join_wave_ignores_member_affiliated_occupants_by_default() -> None:
    bot = DummyProtections()
    bot.protections["JoinWaveShortCircuitProtection"].update({"enabled": True, "max_joins": 1})
    bot.occupants[ROOM]["MemberJoiner"] = {
        "jid": "member-joiner@example.org",
        "role": "participant",
        "affiliation": "member",
    }

    await bot.protection_on_join(ROOM, "MemberJoiner", "member-joiner@example.org")

    assert bot.muc_plugin.room_configs == []
    assert bot.sent == []


@pytest.mark.asyncio
async def test_join_wave_can_count_members_when_configured() -> None:
    bot = DummyProtections()
    bot.protections["JoinWaveShortCircuitProtection"].update({
        "enabled": True,
        "max_joins": 1,
        "ignore_member_affiliations": False,
    })

    await bot.protection_on_join(ROOM, "Alice", "alice@example.org")

    assert bot.muc_plugin.room_configs


@pytest.mark.asyncio
async def test_join_wave_ignores_recent_rejoin_subjects_after_reconnect() -> None:
    bot = DummyProtections()
    bot.protections["JoinWaveShortCircuitProtection"].update({
        "enabled": True,
        "max_joins": 1,
        "rejoin_grace_seconds": 300,
    })
    bot.protection_remember_current_occupants()

    await bot.protection_on_join(ROOM, "Spammer", "spam@example.org")

    assert bot.muc_plugin.room_configs == []
    assert bot.sent == []


@pytest.mark.asyncio
async def test_recent_rejoin_subjects_expire(monkeypatch) -> None:
    bot = DummyProtections()
    bot.protections["JoinWaveShortCircuitProtection"].update({
        "enabled": True,
        "max_joins": 1,
        "rejoin_grace_seconds": 10,
    })
    bot.occupants[ROOM]["Recent"] = {
        "jid": "recent@example.org",
        "role": "participant",
        "affiliation": "none",
    }
    monkeypatch.setattr("banbot.protections.manager.time.time", lambda: 100.0)
    bot.protection_remember_current_occupants()
    monkeypatch.setattr("banbot.protections.checks.time.time", lambda: 111.0)

    await bot.protection_on_join(ROOM, "Recent", "recent@example.org")

    assert bot.muc_plugin.room_configs


@pytest.mark.asyncio
async def test_notify_policy_change_respects_enabled_and_event_toggles() -> None:
    bot = DummyProtections()

    await bot.notify_policy_change("ban_created", actor="admin@example.org", target="spam@example.org", room=ROOM, comment="spam")
    assert "Policy change" in last_body(bot)
    assert "Target: spam@" in last_body(bot)

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


@pytest.mark.asyncio
async def test_join_wave_triggers_at_configured_threshold() -> None:
    bot = DummyProtections()
    bot.protections["JoinWaveShortCircuitProtection"].update({
        "enabled": True,
        "max_joins": 2,
        "window_seconds": 60,
        "cooldown_seconds": 60,
    })

    await bot.protection_on_join(ROOM, "Joiner1", "joiner1@example.org")
    await bot.protection_on_join(ROOM, "Joiner2", "joiner2@example.org")

    assert bot.muc_plugin.room_configs == [
        (
            ROOM,
            {
                "muc#roomconfig_persistentroom": "1",
                "muc#roomconfig_membersonly": "1",
                "muc#roomconfig_moderatedroom": "1",
            },
            "submit",
        )
    ]
    assert "Joins in window: 2" in last_body(bot)


@pytest.mark.asyncio
async def test_join_hooks_ignore_initial_room_roster_population(fake_msg_factory) -> None:
    bot = DummyProtections()
    bot.room_join_time = {ROOM: time.time()}
    bot.protections["JoinWaveShortCircuitProtection"].update({
        "enabled": True,
        "max_joins": 1,
        "startup_grace_seconds": 30,
    })
    bot.protections["FirstMessageMediaProtection"].update({"enabled": True, "action": "ban"})

    await bot.protection_on_join(ROOM, "Existing", "existing@example.org")

    assert bot.muc_plugin.room_configs == []
    assert (ROOM, "existing@example.org") not in bot.protection_joined_at

    bot.occupants[ROOM]["Existing"] = {"jid": "existing@example.org", "role": "participant", "affiliation": "member"}
    msg = fake_msg_factory(room=ROOM, nick="Existing", body="https://upload.example.org/existing.jpg")
    handled = await bot.protections_on_message(msg, ROOM, "Existing", "https://upload.example.org/existing.jpg")

    assert handled is False
    assert bot.bans == []


@pytest.mark.asyncio
async def test_similar_message_triggers_on_repeated_normalized_spam(fake_msg_factory) -> None:
    bot = DummyProtections()
    bot.protections["SimilarMessageProtection"].update({
        "enabled": True,
        "max_similar": 3,
        "window_seconds": 120,
        "similarity_percent": 90,
        "min_length": 10,
        "min_words": 2,
        "action": "ban",
    })
    body = "cheap spam offer visit now"

    first = fake_msg_factory(room=ROOM, nick="Alice", body=body)
    second = fake_msg_factory(room=ROOM, nick="Bob", body=body)
    third = fake_msg_factory(room=ROOM, nick="Spammer", body=body)

    assert await bot.protections_on_message(first, ROOM, "Alice", body) is False
    assert await bot.protections_on_message(second, ROOM, "Bob", body) is False
    assert await bot.protections_on_message(third, ROOM, "Spammer", body) is True

    assert bot.bans == [("spam@example.org", None, "protection:SimilarMessageProtection", "repeated/similar spam detected")]
    assert list(bot.protection_similar_messages[ROOM]) == []


@pytest.mark.asyncio
async def test_similar_message_normalizes_changing_urls(fake_msg_factory) -> None:
    bot = DummyProtections()
    bot.protections["SimilarMessageProtection"].update({
        "enabled": True,
        "max_similar": 2,
        "window_seconds": 120,
        "similarity_percent": 90,
        "min_length": 10,
        "min_words": 3,
        "action": "notify",
    })
    first_body = "claim your free gift now https://one.invalid/a123"
    second_body = "claim your free gift now https://two.invalid/b456"
    first = fake_msg_factory(room=ROOM, nick="Alice", body=first_body)
    second = fake_msg_factory(room=ROOM, nick="Spammer", body=second_body)

    assert await bot.protections_on_message(first, ROOM, "Alice", first_body) is False
    assert await bot.protections_on_message(second, ROOM, "Spammer", second_body) is True

    assert "SimilarMessageProtection triggered" in last_body(bot)
    assert "Action: notify" in last_body(bot)


@pytest.mark.asyncio
async def test_similar_message_ignores_short_messages(fake_msg_factory) -> None:
    bot = DummyProtections()
    bot.protections["SimilarMessageProtection"].update({"enabled": True, "max_similar": 2, "action": "ban"})
    msg = fake_msg_factory(room=ROOM, nick="Spammer", body="ok")

    assert await bot.protections_on_message(msg, ROOM, "Spammer", "ok") is False
    assert bot.bans == []


@pytest.mark.asyncio
async def test_similar_message_window_expires_old_entries(fake_msg_factory, monkeypatch) -> None:
    bot = DummyProtections()
    bot.protections["SimilarMessageProtection"].update({
        "enabled": True,
        "max_similar": 2,
        "window_seconds": 10,
        "min_length": 10,
        "min_words": 2,
        "action": "ban",
    })
    body = "same spam payload here"
    times = iter([100.0, 111.0])
    monkeypatch.setattr("banbot.protections.checks.time.time", lambda: next(times))

    first = fake_msg_factory(room=ROOM, nick="Alice", body=body)
    second = fake_msg_factory(room=ROOM, nick="Spammer", body=body)

    assert await bot.protections_on_message(first, ROOM, "Alice", body) is False
    assert await bot.protections_on_message(second, ROOM, "Spammer", body) is False
    assert bot.bans == []


@pytest.mark.asyncio
async def test_tempban_protection_redacts_target_messages_even_when_reason_is_not_auto_reason(fake_msg_factory) -> None:
    bot = DummyProtections()
    bot.protections["MentionLimitProtection"].update({
        "enabled": True,
        "max_mentions": 1,
        "action": "tempban",
        "tempban_seconds": 120,
        "redact": True,
    })
    msg = fake_msg_factory(room=ROOM, nick="Spammer", body="hi Alice Bob")

    handled = await bot.protections_on_message(msg, ROOM, "Spammer", "hi Alice Bob")

    assert handled is True
    assert bot.bans[0][0] == "spam@example.org"
    assert bot.bans[0][2] == "protection:MentionLimitProtection"
    assert bot.ban_auto_redact_flags == [False]
    assert bot.target_redactions == [
        (
            "spam@example.org",
            "too many mentions",
            "protection:MentionLimitProtection",
            True,
            "Auto-redaction completed after ban",
        )
    ]


@pytest.mark.asyncio
async def test_repeated_punitive_protection_actions_are_suppressed_during_cooldown(monkeypatch) -> None:
    bot = DummyProtections()
    bot.protections["FloodSpamProtection"].update({"action": "ban", "redact": False})
    monkeypatch.setattr("banbot.protections.actions.time.time", lambda: 100.0)

    await bot._protection_apply_action(
        protection="FloodSpamProtection",
        room=ROOM,
        nick="Spammer",
    )
    await bot._protection_apply_action(
        protection="FloodSpamProtection",
        room=ROOM,
        nick="Spammer",
    )

    assert bot.bans == [("spam@example.org", None, "protection:FloodSpamProtection", "spam/flood detected")]
