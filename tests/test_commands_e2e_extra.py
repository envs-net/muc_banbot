"""Higher-level command-routing tests for admin command flows."""

from __future__ import annotations

import time

import pytest

from banbot.commands import CommandMixin
from banbot.messaging import MessagingMixin


class CommandE2EBot(CommandMixin, MessagingMixin):
    def __init__(self):
        self.command_prefix = "!"
        self.protected_rooms = {"room@conference.example.test"}
        self.allow_user_cmds = True
        self.public_command_rate_limit_hits = {}
        self.public_command_rate_limit_window = 30
        self.public_command_rate_limit_max = 3
        self.version_check_url = "https://example.test/releases/latest"
        self.rtbl_enabled = True
        self.last_import_backup_file = None
        self.sent = []
        self.ban_calls = []
        self.unban_calls = []
        self.room_calls = []
        self.audit_calls = []
        self.rtbl_calls = []
        self.ignore_calls = []
        self.sync_calls = []
        self.policy_calls = []
        self.banlist_calls = []
        self.banlist_rtbl_calls = []
        self.bansearch_calls = []
        self.update_result = (False, "2.2.0", None)
        self.export_result = (True, "export ok")
        self.import_result = (0, 0, [])
        self.occupants = {
            "admin@conference.example.test": {
                "Admin": {"jid": "admin@example.test/resource"},
                "Guest": {"jid": "guest@example.test/resource"},
            },
            "room@conference.example.test": {
                "Alice": {"jid": "alice@example.test/resource"},
            },
        }
        self.authorized = True

    async def bot_send_message(self, **kwargs):
        self.sent.append(kwargs)

    def is_authorized(self, msg):
        return self.authorized

    async def _decrypt_incoming_omemo_message(self, msg):
        return msg, False

    async def ban_all(self, target, until, issuer, comment=None):
        self.ban_calls.append((target, until, issuer, comment))

    async def unban_all(self, target, issuer):
        self.unban_calls.append((target, issuer))

    async def cmd_room(self, args, room):
        self.room_calls.append((list(args), room))

    async def cmd_banlist(self, room, page=1, show_all=False):
        self.banlist_calls.append((room, page, show_all))

    async def cmd_banlist_rtbl(self, room, page=1, show_all=False):
        self.banlist_rtbl_calls.append((room, page, show_all))

    async def cmd_bansearch(self, query, page=1, show_all=False):
        self.bansearch_calls.append((query, page, show_all))

    async def cmd_audit(self, args, room):
        self.audit_calls.append((list(args), room))

    async def cmd_rtbl(self, args, room, actor=None):
        self.rtbl_calls.append((list(args), room, actor))

    async def cmd_ignore(self, args, room, actor=None, command_name="ignore"):
        self.ignore_calls.append((list(args), room, actor, command_name))

    async def cmd_policy(self, args, room):
        self.policy_calls.append((list(args), room))

    async def sync_rooms_and_bans(self):
        self.sync_calls.append("sync")

    async def sync_admins(self, announce=False):
        self.sync_calls.append(("syncadmins", announce))

    async def sync_bans(self):
        self.sync_calls.append("syncbans")

    async def check_for_updates_once(self, announce=False):
        return self.update_result

    async def export_bans_to_csv(self):
        return self.export_result

    async def import_bans_from_csv(self, filename):
        self.last_import_backup_file = "backup.csv"
        return self.import_result

    def log_event(self, level, event, **fields):
        self.logged_event = (level, event, fields)

    async def audit_event(self, event_type, **kwargs):
        self.audit_logged_event = (event_type, kwargs)

    async def _cmd_config(self, room):
        self.sent.append({"mto": room, "mbody": "config", "mtype": "groupchat"})

    async def _cmd_reloadconfig(self, room):
        self.sent.append({"mto": room, "mbody": "reload", "mtype": "groupchat"})

    async def _cmd_status(self, room):
        self.sent.append({"mto": room, "mbody": "status", "mtype": "groupchat"})


def admin_msg(fake_msg_factory, body: str, nick: str = "Admin"):
    return fake_msg_factory(
        room="admin@conference.example.test",
        nick=nick,
        body=body,
    )


@pytest.mark.asyncio
async def test_admin_ban_command_routes_to_ban_all_with_real_actor(fake_msg_factory, monkeypatch):
    import banbot.commands as commands

    monkeypatch.setattr(commands, "ADMIN_ROOM", "admin@conference.example.test")
    monkeypatch.setattr(commands, "NICK", "BanBot")
    bot = CommandE2EBot()

    await bot.on_message(admin_msg(fake_msg_factory, "!ban User@Example.org repeated spam"))

    assert bot.ban_calls == [
        ("User@Example.org", None, "admin@example.test/resource", "repeated spam")
    ]


@pytest.mark.asyncio
async def test_admin_tempban_command_parses_duration_and_comment(fake_msg_factory, monkeypatch):
    import banbot.commands as commands

    monkeypatch.setattr(commands, "ADMIN_ROOM", "admin@conference.example.test")
    monkeypatch.setattr(commands, "NICK", "BanBot")
    start = int(time.time())
    bot = CommandE2EBot()

    await bot.on_message(admin_msg(fake_msg_factory, "!tempban user@example.org 10m noisy"))

    target, until, issuer, comment = bot.ban_calls[0]
    assert target == "user@example.org"
    assert start + 590 <= until <= start + 610
    assert issuer == "admin@example.test/resource"
    assert comment == "noisy"


@pytest.mark.asyncio
async def test_admin_tempban_invalid_duration_is_reported(fake_msg_factory, monkeypatch):
    import banbot.commands as commands

    monkeypatch.setattr(commands, "ADMIN_ROOM", "admin@conference.example.test")
    monkeypatch.setattr(commands, "NICK", "BanBot")
    bot = CommandE2EBot()

    await bot.on_message(admin_msg(fake_msg_factory, "!tempban user@example.org forever"))

    assert bot.ban_calls == []
    assert "Invalid duration" in bot.sent[-1]["mbody"]


@pytest.mark.asyncio
async def test_admin_unban_command_routes_to_unban_all(fake_msg_factory, monkeypatch):
    import banbot.commands as commands

    monkeypatch.setattr(commands, "ADMIN_ROOM", "admin@conference.example.test")
    monkeypatch.setattr(commands, "NICK", "BanBot")
    bot = CommandE2EBot()

    await bot.on_message(admin_msg(fake_msg_factory, "!unban user@example.org"))

    assert bot.unban_calls == [("user@example.org", "admin@example.test/resource")]


@pytest.mark.asyncio
async def test_admin_export_and_import_commands_send_summaries(fake_msg_factory, monkeypatch):
    import banbot.commands as commands

    monkeypatch.setattr(commands, "ADMIN_ROOM", "admin@conference.example.test")
    monkeypatch.setattr(commands, "NICK", "BanBot")
    bot = CommandE2EBot()
    bot.import_result = (2, 1, ["line 4: invalid target"])

    await bot.on_message(admin_msg(fake_msg_factory, "!export"))
    await bot.on_message(admin_msg(fake_msg_factory, "!import bans.csv"))

    assert bot.sent[0]["mbody"] == "export ok"
    assert "Successful: 2" in bot.sent[1]["mbody"]
    assert "Skipped: 1" in bot.sent[1]["mbody"]
    assert "line 4: invalid target" in bot.sent[1]["mbody"]
    assert "Backup before import: backup.csv" in bot.sent[1]["mbody"]


@pytest.mark.asyncio
async def test_admin_import_without_filename_shows_usage(fake_msg_factory, monkeypatch):
    import banbot.commands as commands

    monkeypatch.setattr(commands, "ADMIN_ROOM", "admin@conference.example.test")
    monkeypatch.setattr(commands, "NICK", "BanBot")
    bot = CommandE2EBot()

    await bot.on_message(admin_msg(fake_msg_factory, "!import"))

    assert "Usage: !import <filename>" in bot.sent[-1]["mbody"]


@pytest.mark.asyncio
async def test_admin_rtbl_disabled_reports_config_hint(fake_msg_factory, monkeypatch):
    import banbot.commands as commands

    monkeypatch.setattr(commands, "ADMIN_ROOM", "admin@conference.example.test")
    monkeypatch.setattr(commands, "NICK", "BanBot")
    bot = CommandE2EBot()
    bot.rtbl_enabled = False

    await bot.on_message(admin_msg(fake_msg_factory, "!rtbl list"))

    assert bot.rtbl_calls == []
    assert "RTBL is disabled" in bot.sent[-1]["mbody"]


@pytest.mark.asyncio
async def test_admin_rtbl_ignore_policy_and_room_commands_route_with_actor(fake_msg_factory, monkeypatch):
    import banbot.commands as commands

    monkeypatch.setattr(commands, "ADMIN_ROOM", "admin@conference.example.test")
    monkeypatch.setattr(commands, "NICK", "BanBot")
    bot = CommandE2EBot()

    await bot.on_message(admin_msg(fake_msg_factory, "!room add room@example.test"))
    await bot.on_message(admin_msg(fake_msg_factory, "!rtbl list"))
    await bot.on_message(admin_msg(fake_msg_factory, "!ignore add bad@example.test"))
    await bot.on_message(admin_msg(fake_msg_factory, "!whitelist add good@example.test"))
    await bot.on_message(admin_msg(fake_msg_factory, "!whitelist"))
    await bot.on_message(admin_msg(fake_msg_factory, "!policy show"))

    assert bot.room_calls == [(["add", "room@example.test"], "admin@conference.example.test")]
    assert bot.rtbl_calls == [(["list"], "admin@conference.example.test", "admin@example.test/resource")]
    assert bot.ignore_calls == [
        (["add", "bad@example.test"], "admin@conference.example.test", "admin@example.test/resource", "ignore"),
        (["add", "good@example.test"], "admin@conference.example.test", "admin@example.test/resource", "whitelist"),
        ([], "admin@conference.example.test", "admin@example.test/resource", "whitelist"),
    ]
    assert bot.policy_calls == [(["show"], "admin@conference.example.test")]


@pytest.mark.asyncio
async def test_unauthorized_admin_command_is_rejected(fake_msg_factory, monkeypatch):
    import banbot.commands as commands

    monkeypatch.setattr(commands, "ADMIN_ROOM", "admin@conference.example.test")
    monkeypatch.setattr(commands, "NICK", "BanBot")
    bot = CommandE2EBot()
    bot.authorized = False

    await bot.on_message(admin_msg(fake_msg_factory, "!ban user@example.org", nick="Guest"))

    assert bot.ban_calls == []
    assert "not authorized" in bot.sent[-1]["mbody"]



@pytest.mark.asyncio
async def test_admin_paged_commands_parse_all_marker(fake_msg_factory, monkeypatch):
    import banbot.commands as commands

    monkeypatch.setattr(commands, "ADMIN_ROOM", "admin@conference.example.test")
    monkeypatch.setattr(commands, "NICK", "BanBot")
    bot = CommandE2EBot()

    await bot.on_message(admin_msg(fake_msg_factory, "!banlist all"))
    await bot.on_message(admin_msg(fake_msg_factory, "!banlist rtbl all"))
    await bot.on_message(admin_msg(fake_msg_factory, "!bansearch all spam wave"))
    await bot.on_message(admin_msg(fake_msg_factory, "!bansearch spam wave all"))
    await bot.on_message(admin_msg(fake_msg_factory, "!audit all spam"))
    await bot.on_message(admin_msg(fake_msg_factory, "!room list all"))

    assert bot.banlist_calls[-1] == ("admin@conference.example.test", 1, True)
    assert bot.banlist_rtbl_calls[-1] == ("admin@conference.example.test", 1, True)
    assert bot.bansearch_calls[-2] == ("spam wave", 1, True)
    assert bot.bansearch_calls[-1] == ("spam wave", 1, True)
    assert bot.audit_calls[-1] == (["all", "spam"], "admin@conference.example.test")
    assert bot.room_calls[-1] == (["list", "all"], "admin@conference.example.test")


@pytest.mark.asyncio
async def test_admin_incomplete_commands_show_usage(fake_msg_factory, monkeypatch):
    import banbot.commands as commands

    monkeypatch.setattr(commands, "ADMIN_ROOM", "admin@conference.example.test")
    monkeypatch.setattr(commands, "NICK", "BanBot")
    bot = CommandE2EBot()

    cases = [
        ("!ban", "Usage: !ban <jid|nick|*.domain.tld> [comment]"),
        ("!tempban", "Usage: !tempban <jid|nick> <10m|2h|1d> [comment]"),
        ("!unban", "Usage: !unban <jid|nick|*.domain.tld>"),
        ("!bansearch", "Usage: !bansearch <query> [all|page|last]"),
        ("!bansearch all", "Usage: !bansearch <query> [all|page|last]"),
        ("!room", "!room list [all|page]"),
    ]

    for body, expected in cases:
        before = len(bot.sent)
        await bot.on_message(admin_msg(fake_msg_factory, body))
        assert len(bot.sent) == before + 1
        assert expected in bot.sent[-1]["mbody"]

    assert bot.ban_calls == []
    assert bot.unban_calls == []
    assert bot.bansearch_calls == []
    assert bot.room_calls == []


@pytest.mark.asyncio
async def test_why_without_target_shows_usage_in_admin_room(fake_msg_factory, monkeypatch):
    import banbot.commands as commands

    monkeypatch.setattr(commands, "ADMIN_ROOM", "admin@conference.example.test")
    monkeypatch.setattr(commands, "NICK", "BanBot")
    bot = CommandE2EBot()

    await bot.on_message(admin_msg(fake_msg_factory, "!why"))

    assert bot.sent[-1]["mbody"] == "❌ Usage: !why <nick|jid>"
