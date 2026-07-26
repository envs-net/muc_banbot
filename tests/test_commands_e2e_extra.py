"""Higher-level command-routing tests for admin command flows."""

from __future__ import annotations

import importlib
import asyncio
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
        self.last_database_backup_file = None
        self._database_file_operation_lock = asyncio.Lock()
        self._ban_state_operation_lock = asyncio.Lock()
        self.sent = []
        self.ban_calls = []
        self.ban_notify_policy_flags = []
        self.unban_calls = []
        self.unban_notify_policy_flags = []
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
        self.flushed_redaction = False
        self.stopped_background_tasks = False
        self.disconnect_calls = []

    async def bot_send_message(self, **kwargs):
        self.sent.append(kwargs)

    def is_authorized(self, msg):
        return self.authorized

    async def _decrypt_incoming_omemo_message(self, msg):
        return msg, False

    async def ban_all(self, target, until, issuer, comment=None, *, auto_redact=True, notify_policy=True):
        self.ban_calls.append((target, until, issuer, comment))
        self.ban_notify_policy_flags.append(notify_policy)

    async def unban_all(self, target, issuer, *, notify_policy=True):
        self.unban_calls.append((target, issuer))
        self.unban_notify_policy_flags.append(notify_policy)

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

    async def import_bans_from_csv(self, filename, *, actor=None, dry_run=False):
        self.import_filename = filename
        self.import_actor = actor
        self.import_dry_run = dry_run
        if not dry_run:
            self.last_database_backup_file = "backup.csv"
        return self.import_result

    def log_event(self, level, event, **fields):
        self.logged_event = (level, event, fields)

    async def audit_event(self, event_type, **kwargs):
        self.audit_logged_event = (event_type, kwargs)

    async def _cmd_config(self, room, args=None, actor=None):
        self.config_call = (room, list(args or []), actor)
        self.sent.append({"mto": room, "mbody": "config", "mtype": "groupchat"})

    async def _cmd_reloadconfig(self, room):
        self.sent.append({"mto": room, "mbody": "reload", "mtype": "groupchat"})

    async def _cmd_status(self, room):
        self.sent.append({"mto": room, "mbody": "status", "mtype": "groupchat"})

    async def flush_redaction_index(self):
        self.flushed_redaction = True

    async def stop_background_tasks(self):
        self.stopped_background_tasks = True

    def disconnect(self, wait=False):
        self.disconnect_calls.append(wait)


def admin_msg(fake_msg_factory, body: str, nick: str = "Admin"):
    return fake_msg_factory(
        room="admin@conference.example.test",
        nick=nick,
        body=body,
    )


@pytest.mark.asyncio
async def test_admin_ban_command_routes_to_ban_all_with_real_actor(fake_msg_factory, monkeypatch):
    commands = importlib.import_module("banbot.commands")

    monkeypatch.setattr(commands, "ADMIN_ROOM", "admin@conference.example.test")
    monkeypatch.setattr(commands, "NICK", "BanBot")
    bot = CommandE2EBot()

    await bot.on_message(admin_msg(fake_msg_factory, "!ban User@Example.org repeated spam"))

    assert bot.ban_calls == [
        ("User@Example.org", None, "admin@example.test/resource", "repeated spam")
    ]
    assert bot.ban_notify_policy_flags == [False]


@pytest.mark.asyncio
async def test_admin_tempban_command_parses_duration_and_comment(fake_msg_factory, monkeypatch):
    commands = importlib.import_module("banbot.commands")

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
    assert bot.ban_notify_policy_flags == [False]


@pytest.mark.asyncio
async def test_admin_tempban_invalid_duration_is_reported(fake_msg_factory, monkeypatch):
    commands = importlib.import_module("banbot.commands")

    monkeypatch.setattr(commands, "ADMIN_ROOM", "admin@conference.example.test")
    monkeypatch.setattr(commands, "NICK", "BanBot")
    bot = CommandE2EBot()

    await bot.on_message(admin_msg(fake_msg_factory, "!tempban user@example.org forever"))

    assert bot.ban_calls == []
    assert "Invalid duration format" in bot.sent[-1]["mbody"]


@pytest.mark.asyncio
async def test_admin_unban_command_routes_to_unban_all(fake_msg_factory, monkeypatch):
    commands = importlib.import_module("banbot.commands")

    monkeypatch.setattr(commands, "ADMIN_ROOM", "admin@conference.example.test")
    monkeypatch.setattr(commands, "NICK", "BanBot")
    bot = CommandE2EBot()

    await bot.on_message(admin_msg(fake_msg_factory, "!unban user@example.org"))

    assert bot.unban_calls == [("user@example.org", "admin@example.test/resource")]
    assert bot.unban_notify_policy_flags == [False]


@pytest.mark.asyncio
async def test_admin_export_and_import_commands_send_summaries(fake_msg_factory, monkeypatch):
    commands = importlib.import_module("banbot.commands")

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
    assert "Full backup before import: backup.csv" in bot.sent[1]["mbody"]


@pytest.mark.asyncio
async def test_admin_import_without_filename_shows_usage(fake_msg_factory, monkeypatch):
    commands = importlib.import_module("banbot.commands")

    monkeypatch.setattr(commands, "ADMIN_ROOM", "admin@conference.example.test")
    monkeypatch.setattr(commands, "NICK", "BanBot")
    bot = CommandE2EBot()

    await bot.on_message(admin_msg(fake_msg_factory, "!import"))

    assert "❌ Usage: !import <filename> [dryrun]" in bot.sent[-1]["mbody"]


@pytest.mark.asyncio
async def test_admin_import_dryrun_command_path_is_exercised(fake_msg_factory, monkeypatch):
    commands = importlib.import_module("banbot.commands")

    monkeypatch.setattr(commands, "ADMIN_ROOM", "admin@conference.example.test")
    monkeypatch.setattr(commands, "NICK", "BanBot")
    bot = CommandE2EBot()
    bot.import_result = (2, 1, ["line 2: skipped in dry run"])

    await bot.on_message(admin_msg(fake_msg_factory, "!import bans.csv dryrun"))

    assert bot.import_filename == "bans.csv"
    assert bot.import_actor == "admin@example.test/resource"
    assert bot.import_dry_run is True
    body = bot.sent[-1]["mbody"]
    assert "Import Dry-Run Results" in body
    assert "Successful: 2" in body
    assert "Skipped: 1" in body
    assert "No backup created and no database changes made." in body
    assert "line 2: skipped in dry run" in body


@pytest.mark.asyncio
async def test_admin_updatecheck_alias_routes_like_checkupdate(fake_msg_factory, monkeypatch):
    commands = importlib.import_module("banbot.commands")

    monkeypatch.setattr(commands, "ADMIN_ROOM", "admin@conference.example.test")
    monkeypatch.setattr(commands, "NICK", "BanBot")
    bot = CommandE2EBot()
    bot.update_result = (True, "2.3.0", None)

    await bot.on_message(admin_msg(fake_msg_factory, "!updatecheck"))

    assert "New bot version available: 2.3.0" in bot.sent[-1]["mbody"]

    bot.update_result = (False, "2.2.0", None)
    await bot.on_message(admin_msg(fake_msg_factory, "!checkupdate"))

    assert "Bot is up to date" in bot.sent[-1]["mbody"]


@pytest.mark.asyncio
async def test_admin_reload_alias_routes_to_reloadconfig(fake_msg_factory, monkeypatch):
    commands = importlib.import_module("banbot.commands")

    monkeypatch.setattr(commands, "ADMIN_ROOM", "admin@conference.example.test")
    monkeypatch.setattr(commands, "NICK", "BanBot")
    bot = CommandE2EBot()

    await bot.on_message(admin_msg(fake_msg_factory, "!reload"))

    assert bot.sent[-1]["mbody"] == "reload"


@pytest.mark.asyncio
async def test_admin_restart_requires_confirmation(fake_msg_factory, monkeypatch):
    commands = importlib.import_module("banbot.commands")

    monkeypatch.setattr(commands, "ADMIN_ROOM", "admin@conference.example.test")
    monkeypatch.setattr(commands, "NICK", "BanBot")
    bot = CommandE2EBot()

    await bot.on_message(admin_msg(fake_msg_factory, "!restart"))

    assert "restart confirm" in bot.sent[-1]["mbody"]
    assert bot.disconnect_calls == []
    assert bot.flushed_redaction is False
    assert bot.stopped_background_tasks is False


@pytest.mark.asyncio
async def test_admin_restart_confirm_flushes_stops_disconnects_and_exits(fake_msg_factory, monkeypatch):
    commands = importlib.import_module("banbot.commands")

    monkeypatch.setattr(commands, "ADMIN_ROOM", "admin@conference.example.test")
    monkeypatch.setattr(commands, "NICK", "BanBot")

    created_tasks = []

    def fake_create_task(coro):
        created_tasks.append(coro)
        return object()

    async def fake_sleep(_delay):
        return None

    def fake_exit(code=0):
        raise SystemExit(code)

    monkeypatch.setattr(commands.asyncio, "create_task", fake_create_task)
    monkeypatch.setattr(commands.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(commands.os, "_exit", fake_exit)

    bot = CommandE2EBot()

    await bot.on_message(admin_msg(fake_msg_factory, "!restart confirm"))

    assert "Restart confirmed" in bot.sent[-1]["mbody"]
    assert bot.sent[-1]["encrypted"] is False
    assert len(created_tasks) == 1

    restart_task = created_tasks[0]
    with pytest.raises(SystemExit) as excinfo:
        await asyncio.wait_for(restart_task, timeout=1)

    assert excinfo.value.code == 75
    assert bot.flushed_redaction is True
    assert bot.stopped_background_tasks is True
    assert bot.disconnect_calls == [False]


@pytest.mark.asyncio
async def test_admin_rtbl_disabled_reports_config_hint(fake_msg_factory, monkeypatch):
    commands = importlib.import_module("banbot.commands")

    monkeypatch.setattr(commands, "ADMIN_ROOM", "admin@conference.example.test")
    monkeypatch.setattr(commands, "NICK", "BanBot")
    bot = CommandE2EBot()
    bot.rtbl_enabled = False

    await bot.on_message(admin_msg(fake_msg_factory, "!rtbl list"))

    assert bot.rtbl_calls == []
    assert "RTBL is disabled" in bot.sent[-1]["mbody"]


@pytest.mark.asyncio
async def test_admin_rtbl_ignore_policy_and_room_commands_route_with_actor(fake_msg_factory, monkeypatch):
    commands = importlib.import_module("banbot.commands")

    monkeypatch.setattr(commands, "ADMIN_ROOM", "admin@conference.example.test")
    monkeypatch.setattr(commands, "NICK", "BanBot")
    bot = CommandE2EBot()

    await bot.on_message(admin_msg(fake_msg_factory, "!room add room@example.test"))
    await bot.on_message(admin_msg(fake_msg_factory, "!rtbl list"))
    await bot.on_message(admin_msg(fake_msg_factory, "!ignore add bad@example.test"))
    await bot.on_message(admin_msg(fake_msg_factory, "!whitelist add good@example.test"))
    await bot.on_message(admin_msg(fake_msg_factory, "!whitelist"))
    await bot.on_message(admin_msg(fake_msg_factory, "!policy show"))
    await bot.on_message(admin_msg(fake_msg_factory, "!rules show"))

    assert bot.room_calls == [(["add", "room@example.test"], "admin@conference.example.test")]
    assert bot.rtbl_calls == [(["list"], "admin@conference.example.test", "admin@example.test/resource")]
    assert bot.ignore_calls == [
        (["add", "bad@example.test"], "admin@conference.example.test", "admin@example.test/resource", "ignore"),
        (["add", "good@example.test"], "admin@conference.example.test", "admin@example.test/resource", "whitelist"),
        ([], "admin@conference.example.test", "admin@example.test/resource", "whitelist"),
    ]
    assert bot.policy_calls == [
        (["show"], "admin@conference.example.test"),
        (["show"], "admin@conference.example.test"),
    ]


@pytest.mark.asyncio
async def test_unauthorized_admin_command_is_rejected(fake_msg_factory, monkeypatch):
    commands = importlib.import_module("banbot.commands")

    monkeypatch.setattr(commands, "ADMIN_ROOM", "admin@conference.example.test")
    monkeypatch.setattr(commands, "NICK", "BanBot")
    bot = CommandE2EBot()
    bot.authorized = False

    await bot.on_message(admin_msg(fake_msg_factory, "!ban user@example.org", nick="Guest"))

    assert bot.ban_calls == []
    assert "not authorized" in bot.sent[-1]["mbody"]



@pytest.mark.asyncio
async def test_admin_paged_commands_parse_all_marker(fake_msg_factory, monkeypatch):
    commands = importlib.import_module("banbot.commands")

    monkeypatch.setattr(commands, "ADMIN_ROOM", "admin@conference.example.test")
    monkeypatch.setattr(commands, "NICK", "BanBot")
    bot = CommandE2EBot()

    await bot.on_message(admin_msg(fake_msg_factory, "!banlist all"))
    await bot.on_message(admin_msg(fake_msg_factory, "!banlist rtbl all"))
    await bot.on_message(admin_msg(fake_msg_factory, "!blacklist all"))
    await bot.on_message(admin_msg(fake_msg_factory, "!blacklist rtbl all"))
    await bot.on_message(admin_msg(fake_msg_factory, "!bansearch all spam wave"))
    await bot.on_message(admin_msg(fake_msg_factory, "!bansearch spam wave all"))
    await bot.on_message(admin_msg(fake_msg_factory, "!audit all spam"))
    await bot.on_message(admin_msg(fake_msg_factory, "!room list all"))

    assert bot.banlist_calls[-2] == ("admin@conference.example.test", 1, True)
    assert bot.banlist_rtbl_calls[-2] == ("admin@conference.example.test", 1, True)
    assert bot.banlist_calls[-1] == ("admin@conference.example.test", 1, True)
    assert bot.banlist_rtbl_calls[-1] == ("admin@conference.example.test", 1, True)
    assert bot.bansearch_calls[-2] == ("spam wave", 1, True)
    assert bot.bansearch_calls[-1] == ("spam wave", 1, True)
    assert bot.audit_calls[-1] == (["all", "spam"], "admin@conference.example.test")
    assert bot.room_calls[-1] == (["list", "all"], "admin@conference.example.test")


@pytest.mark.asyncio
async def test_admin_incomplete_commands_show_usage(fake_msg_factory, monkeypatch):
    commands = importlib.import_module("banbot.commands")

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
    commands = importlib.import_module("banbot.commands")

    monkeypatch.setattr(commands, "ADMIN_ROOM", "admin@conference.example.test")
    monkeypatch.setattr(commands, "NICK", "BanBot")
    bot = CommandE2EBot()

    await bot.on_message(admin_msg(fake_msg_factory, "!why"))

    assert bot.sent[-1]["mbody"] == "❌ Usage: !why <nick|jid>"


class PolicyCommandBot(CommandMixin, MessagingMixin):
    def __init__(self):
        self.command_prefix = "!"
        self.protected_rooms = {"room@conference.example.test"}
        self.allow_user_cmds = True
        self.public_command_rate_limit_hits = {}
        self.public_command_rate_limit_window = 30
        self.public_command_rate_limit_max = 3
        self.sent = []
        self.policy_enabled = False
        self.policy_text = ""

    async def bot_send_message(self, **kwargs):
        self.sent.append(kwargs)

    async def _decrypt_incoming_omemo_message(self, msg):
        return msg, False

    def is_authorized(self, msg):
        return True

    async def get_public_policy(self):
        return self.policy_enabled, self.policy_text

    async def set_public_policy_text(self, text, enabled=True):
        self.policy_text = text
        self.policy_enabled = enabled

    async def set_public_policy_enabled(self, enabled):
        self.policy_enabled = enabled

    async def clear_public_policy(self):
        self.policy_enabled = False
        self.policy_text = ""


@pytest.mark.asyncio
async def test_policy_without_text_shows_usage(fake_msg_factory, monkeypatch):
    commands = importlib.import_module("banbot.commands")

    monkeypatch.setattr(commands, "ADMIN_ROOM", "admin@conference.example.test")
    monkeypatch.setattr(commands, "NICK", "adminbot")
    bot = PolicyCommandBot()

    await bot.on_message(admin_msg(fake_msg_factory, "!policy"))

    body = bot.sent[-1]["mbody"]
    assert "No public policy text is configured" in body
    assert "Usage:" in body
    assert "!policy show" in body
    assert "!policy set <text>" in body


@pytest.mark.asyncio
async def test_policy_help_shows_usage(fake_msg_factory, monkeypatch):
    commands = importlib.import_module("banbot.commands")

    monkeypatch.setattr(commands, "ADMIN_ROOM", "admin@conference.example.test")
    monkeypatch.setattr(commands, "NICK", "adminbot")
    bot = PolicyCommandBot()

    await bot.on_message(admin_msg(fake_msg_factory, "!policy help"))

    body = bot.sent[-1]["mbody"]
    assert "Usage:" in body
    assert "!policy show" in body
    assert "!policy clear" in body
    assert "Supported placeholders" in body


@pytest.mark.asyncio
async def test_policy_set_without_text_shows_usage(fake_msg_factory, monkeypatch):
    commands = importlib.import_module("banbot.commands")

    monkeypatch.setattr(commands, "ADMIN_ROOM", "admin@conference.example.test")
    monkeypatch.setattr(commands, "NICK", "adminbot")
    bot = PolicyCommandBot()

    await bot.on_message(admin_msg(fake_msg_factory, "!policy set"))

    body = bot.sent[-1]["mbody"]
    assert "Usage: !policy set <text>" in body
    assert "Use literal" in body


@pytest.mark.asyncio
async def test_policy_show_with_existing_text_includes_commands(fake_msg_factory, monkeypatch):
    commands = importlib.import_module("banbot.commands")

    monkeypatch.setattr(commands, "ADMIN_ROOM", "admin@conference.example.test")
    monkeypatch.setattr(commands, "NICK", "adminbot")
    bot = PolicyCommandBot()
    bot.policy_enabled = True
    bot.policy_text = "Use {prefix}why <nick> in {room}."

    await bot.on_message(admin_msg(fake_msg_factory, "!policy"))

    body = bot.sent[-1]["mbody"]
    assert "Public policy is currently enabled" in body
    assert "Use !why <nick> in admin@conference.example.test." in body
    assert "Commands:" in body
    assert "!policy disable" in body

@pytest.mark.asyncio
async def test_rules_alias_works_in_admin_room(fake_msg_factory, monkeypatch):
    commands = importlib.import_module("banbot.commands")

    monkeypatch.setattr(commands, "ADMIN_ROOM", "admin@conference.example.test")
    monkeypatch.setattr(commands, "NICK", "adminbot")

    bot = PolicyCommandBot()
    bot.policy_enabled = True
    bot.policy_text = "Please read the rules."

    await bot.on_message(admin_msg(fake_msg_factory, "!rules"))

    body = bot.sent[-1]["mbody"]
    assert "Public policy is currently enabled" in body
    assert "Please read the rules." in body

@pytest.mark.asyncio
async def test_policy_delete_and_remove_alias_clear_text(fake_msg_factory, monkeypatch):
    commands = importlib.import_module("banbot.commands")

    monkeypatch.setattr(commands, "ADMIN_ROOM", "admin@conference.example.test")
    monkeypatch.setattr(commands, "NICK", "adminbot")

    bot = PolicyCommandBot()
    bot.policy_enabled = True
    bot.policy_text = "Please read the rules."

    await bot.on_message(admin_msg(fake_msg_factory, "!policy delete"))
    assert bot.policy_enabled is False
    assert bot.policy_text == ""
    assert "cleared" in bot.sent[-1]["mbody"]

    bot.policy_enabled = True
    bot.policy_text = "Please read the rules again."
    await bot.on_message(admin_msg(fake_msg_factory, "!policy remove"))
    assert bot.policy_enabled is False
    assert bot.policy_text == ""
    assert "cleared" in bot.sent[-1]["mbody"]

@pytest.mark.asyncio
async def test_admin_help_room_shows_focused_room_usage(fake_msg_factory, monkeypatch):
    commands = importlib.import_module("banbot.commands")

    monkeypatch.setattr(commands, "ADMIN_ROOM", "admin@conference.example.test")
    monkeypatch.setattr(commands, "NICK", "BanBot")
    bot = CommandE2EBot()

    await bot.on_message(admin_msg(fake_msg_factory, "!help room"))

    body = bot.sent[-1]["mbody"]
    assert "Usage:" in body
    assert "!room list [all|page]" in body
    assert "!room invite accept <id>" in body
    assert bot.room_calls == []


@pytest.mark.asyncio
async def test_admin_help_redact_shows_focused_redact_usage_without_running_command(fake_msg_factory, monkeypatch):
    commands = importlib.import_module("banbot.commands")

    monkeypatch.setattr(commands, "ADMIN_ROOM", "admin@conference.example.test")
    monkeypatch.setattr(commands, "NICK", "BanBot")
    bot = CommandE2EBot()

    await bot.on_message(admin_msg(fake_msg_factory, "!help redact"))

    body = bot.sent[-1]["mbody"]
    assert "Usage:" in body
    assert "!redact <jid> [reason]" in body
    assert "!redact id <room_jid> <stanza_id> [reason]" in body
    assert "!redact cleanup" in body


@pytest.mark.asyncio
async def test_admin_help_topic_aliases_and_unknown_topic(fake_msg_factory, monkeypatch):
    commands = importlib.import_module("banbot.commands")

    monkeypatch.setattr(commands, "ADMIN_ROOM", "admin@conference.example.test")
    monkeypatch.setattr(commands, "NICK", "BanBot")
    bot = CommandE2EBot()

    await bot.on_message(admin_msg(fake_msg_factory, "!help blacklist"))
    assert "!banlist [all|page|last]" in bot.sent[-1]["mbody"]
    assert "alias for !banlist" in bot.sent[-1]["mbody"]

    await bot.on_message(admin_msg(fake_msg_factory, "!help does-not-exist"))
    assert "Unknown help topic" in bot.sent[-1]["mbody"]
    assert "Use !help" in bot.sent[-1]["mbody"]

@pytest.mark.asyncio
async def test_admin_help_all_command_topics_have_focused_usage(fake_msg_factory, monkeypatch):
    commands = importlib.import_module("banbot.commands")

    monkeypatch.setattr(commands, "ADMIN_ROOM", "admin@conference.example.test")
    monkeypatch.setattr(commands, "NICK", "BanBot")
    bot = CommandE2EBot()

    expected = {
        "help": "!help <command>",
        "status": "!status",
        "config": "!config show [all|page|last]",
        "reload": "!reload / !reloadconfig",
        "reloadconfig": "!reload / !reloadconfig",
        "restart": "!restart confirm",
        "checkupdate": "!checkupdate / !updatecheck",
        "updatecheck": "!checkupdate / !updatecheck",
        "whoami": "!whoami",
        "audit": "!audit [all|page|last|query]",
        "backup": "!backup list [all|page|last]",
        "restore": "!restore <filename|latest> confirm",
        "room": "!room list [all|page]",
        "room invite": "!room invite cleanup [expired]",
        "invite": "!room invite accept <id>",
        "policy": "!policy show",
        "rules": "!policy show",
        "ban": "!ban <jid|nick|*.domain.tld> [comment]",
        "tempban": "!tempban <jid|nick> <10m|2h|1d> [comment]",
        "unban": "!unban <jid|nick|*.domain.tld>",
        "redact": "!redact cleanup",
        "banlist": "!banlist [all|page|last]",
        "blacklist": "!blacklist ... - alias for !banlist",
        "bansearch": "!bansearch <query> [all|page|last]",
        "why": "!why <nick|jid>",
        "sync": "!sync",
        "syncadmins": "!syncadmins",
        "syncbans": "!syncbans",
        "ignore": "!ignore add <jid|domain> [reason]",
        "whitelist": "!whitelist ... - alias for !ignore",
        "omemo": "!omemo reset [confirm]",
        "rtbl": "!rtbl refresh [service_jid] [node]",
        "rtbl publish": "!rtbl publish sync",
        "export": "!export list [all|page|last]",
        "import": "!import <filename> [dryrun]",
    }

    for topic, expected_text in expected.items():
        await bot.on_message(admin_msg(fake_msg_factory, f"!help {topic}"))
        body = bot.sent[-1]["mbody"]
        assert "Usage:" in body, topic
        assert expected_text in body, topic
        assert "Unknown help topic" not in body, topic

    await bot.on_message(admin_msg(fake_msg_factory, "!help config"))
    assert "!config search/find <query>" in bot.sent[-1]["mbody"]
    assert "!config diff [all|page|last]" in bot.sent[-1]["mbody"]


@pytest.mark.asyncio
async def test_help_subtopics_do_not_execute_commands(fake_msg_factory, monkeypatch):
    commands = importlib.import_module("banbot.commands")

    monkeypatch.setattr(commands, "ADMIN_ROOM", "admin@conference.example.test")
    monkeypatch.setattr(commands, "NICK", "BanBot")
    bot = CommandE2EBot()

    await bot.on_message(admin_msg(fake_msg_factory, "!help room invite"))
    await bot.on_message(admin_msg(fake_msg_factory, "!help rtbl publish"))

    assert bot.room_calls == []
    assert bot.rtbl_calls == []
    assert "!rtbl publish status" in bot.sent[-1]["mbody"]


@pytest.mark.asyncio
async def test_admin_help_default_all_and_paginated_mode(fake_msg_factory, monkeypatch):
    commands = importlib.import_module("banbot.commands")

    monkeypatch.setattr(commands, "ADMIN_ROOM", "admin@conference.example.test")
    monkeypatch.setattr(commands, "NICK", "BanBot")
    bot = CommandE2EBot()
    bot.list_page_size = 6
    bot.help_output_mode = "all"

    await bot.on_message(admin_msg(fake_msg_factory, "!help"))
    assert "Admin Help (page" not in bot.sent[-1]["mbody"]
    assert "🛡️ RTBL" in bot.sent[-1]["mbody"]
    assert "🛡️ Protections" in bot.sent[-1]["mbody"]
    assert "!protections list [all|page|last]" in bot.sent[-1]["mbody"]
    assert "!protection enable/disable <name>" in bot.sent[-1]["mbody"]
    assert "!protections reporters add/remove/list <jid>" in bot.sent[-1]["mbody"]
    assert "!config [all|page|last] / show/search/find/diff/set/unset" in bot.sent[-1]["mbody"]
    assert (
        "!banlist rtbl / !blacklist rtbl [all|page|last]"
        in bot.sent[-1]["mbody"]
    )

    bot.help_output_mode = "paginate"
    await bot.on_message(admin_msg(fake_msg_factory, "!help"))
    body = bot.sent[-1]["mbody"]
    assert "🛠️ Admin Help (page 1/" in body
    assert "Use !help all for the full output." in body

    await bot.on_message(admin_msg(fake_msg_factory, "!help all"))
    assert "Admin Help (page" not in bot.sent[-1]["mbody"]
    assert "🛡️ RTBL" in bot.sent[-1]["mbody"]
    assert "🛡️ Protections" in bot.sent[-1]["mbody"]

    await bot.on_message(admin_msg(fake_msg_factory, "!help room"))
    assert "Usage:" in bot.sent[-1]["mbody"]
    assert "!room list [all|page]" in bot.sent[-1]["mbody"]


@pytest.mark.asyncio
async def test_config_command_passes_page_args_to_config_handler(fake_msg_factory, monkeypatch):
    commands = importlib.import_module("banbot.commands")

    monkeypatch.setattr(commands, "ADMIN_ROOM", "admin@conference.example.test")
    monkeypatch.setattr(commands, "NICK", "BanBot")
    bot = CommandE2EBot()

    await bot.on_message(admin_msg(fake_msg_factory, "!config 2"))

    assert bot.config_call[1] == ["2"]
