import importlib
import asyncio
import time

import pytest

pytest.importorskip("slixmpp")
aiosqlite = pytest.importorskip("aiosqlite")

from banbot.cache import CacheMixin
from banbot.db import DatabaseMixin
from banbot.moderation import ModerationMixin
from banbot.utils import bare_jid


class FlowBot(ModerationMixin, DatabaseMixin, CacheMixin):
    def __init__(self):
        self.protected_rooms = {"room@conference.example.test"}
        self.occupants = {
            "room@conference.example.test": {
                "Nick": {"jid": "user@example.org/resource", "affiliation": "member"},
            }
        }
        self.ban_cache = {}
        self.ban_index_by_jid = {}
        self.ban_index_by_nick = {}
        self.ban_index_by_domain = {}
        self.max_tempban_days = 30
        self.allow_user_cmds = True
        self.show_ban_in_muc = True
        self.muc_write_semaphore = asyncio.Semaphore(10)
        self.sent = []
        self.applied_bans = []
        self.applied_unbans = []
        self.published = []
        self.retracted = []
        self.events = []
        self.audit_events = []
        self.auto_redactions = []
        self.protect_result = (False, None)

    def bare_jid(self, jid):
        return bare_jid(jid)

    def is_ignored_target(self, *args, **kwargs):
        return False

    async def is_protected_admin_target(self, *args, **kwargs):
        return self.protect_result

    async def apply_ban_to_room(self, room, ban_jid, ban_nick, comment, issuer=None, announce_missing_rights=True):
        self.applied_bans.append((room, ban_jid, ban_nick, comment, issuer))

    async def apply_unban_to_room(self, room, ban_jid, ban_nick, domain=None, *, announce=True):
        self.applied_unbans.append((room, ban_jid, ban_nick, domain, announce))
        return True

    async def rtbl_publish_ban(self, jid=None, domain=None, comment=None):
        self.published.append((jid, domain, comment))

    async def rtbl_retract_ban(self, jid=None, domain=None):
        self.retracted.append((jid, domain))

    def log_event(self, level, event, **fields):
        self.events.append((level, event, fields))

    async def audit_event(self, event_type, **kwargs):
        self.audit_events.append((event_type, kwargs))

    async def bot_send_message(self, **kwargs):
        self.sent.append(kwargs)

    async def maybe_auto_redact_after_ban(self, jid, comment, actor=None):
        self.auto_redactions.append((jid, comment, actor))


async def make_bot():
    bot = FlowBot()
    await bot.setup_db()
    return bot


@pytest.mark.asyncio
async def test_ban_all_persists_publishes_and_applies_to_protected_rooms(temp_db_path, monkeypatch):
    moderation_module = importlib.import_module("banbot.moderation")

    monkeypatch.setattr(moderation_module, "ADMIN_ROOM", "admin@conference.example.test")
    bot = await make_bot()
    try:
        await bot.ban_all("Other@Example.org", None, issuer="admin@example.test", comment="spam")

        assert "other@example.org" in bot.ban_cache
        assert bot.applied_bans == [
            ("room@conference.example.test", "other@example.org", None, "spam", "admin@example.test")
        ]
        assert bot.published == [("other@example.org", None, "spam")]
        assert any("✅ Banned other@example.org" in msg["mbody"] for msg in bot.sent)
    finally:
        await bot.db.close()


@pytest.mark.asyncio
async def test_permanent_ban_runs_command_auto_redaction(temp_db_path, monkeypatch):
    moderation_module = importlib.import_module("banbot.moderation")

    monkeypatch.setattr(moderation_module, "ADMIN_ROOM", "admin@conference.example.test")
    bot = await make_bot()
    try:
        await bot.ban_all("user@example.org", None, issuer="admin@example.test", comment="spam")

        # Command-level redaction intentionally runs outside the ban command
        # path. Wait for the tracked operation before asserting its result.
        tasks = list(bot.redaction_operation_tasks)
        if tasks:
            await asyncio.gather(*tasks)

        assert bot.auto_redactions == [("user@example.org", "spam", "admin@example.test")]
        assert "user@example.org" in bot.ban_cache

        cursor = await bot.db.execute(
            "SELECT jid, nick, comment, issuer FROM bans WHERE jid = ?",
            ("user@example.org",),
        )
        row = await cursor.fetchone()
        assert row is not None
        assert row[0] == "user@example.org"
        assert row[1] == "nick"
        assert row[2] == "spam"
        assert row[3] == "admin@example.test"
    finally:
        await bot.db.close()


@pytest.mark.asyncio
async def test_ban_ack_precedes_room_enforcement_and_auto_redaction(
    temp_db_path,
    monkeypatch,
):
    moderation_module = importlib.import_module("banbot.moderation")

    monkeypatch.setattr(moderation_module, "ADMIN_ROOM", "admin@conference.example.test")
    bot = await make_bot()
    room_started = asyncio.Event()
    release_room = asyncio.Event()
    redaction_started = asyncio.Event()

    async def slow_apply_ban_to_room(*args, **kwargs):
        room_started.set()
        await release_room.wait()

    async def observe_redaction(jid, comment, actor=None):
        redaction_started.set()
        bot.auto_redactions.append((jid, comment, actor))

    bot.apply_ban_to_room = slow_apply_ban_to_room
    bot.maybe_auto_redact_after_ban = observe_redaction
    ban_task = asyncio.create_task(
        bot.ban_all(
            "user@example.org",
            None,
            issuer="admin@example.test",
            comment="spam",
        )
    )
    try:
        await asyncio.wait_for(room_started.wait(), timeout=1)

        assert any("✅ Banned user@example.org" in msg["mbody"] for msg in bot.sent)
        assert redaction_started.is_set() is False

        release_room.set()
        await asyncio.wait_for(ban_task, timeout=1)
        await asyncio.wait_for(redaction_started.wait(), timeout=1)
    finally:
        release_room.set()
        await asyncio.gather(ban_task, return_exceptions=True)
        tasks = list(getattr(bot, "redaction_operation_tasks", set()))
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await bot.db.close()


@pytest.mark.asyncio
async def test_ban_applies_to_protected_rooms_concurrently(temp_db_path, monkeypatch):
    moderation_module = importlib.import_module("banbot.moderation")

    monkeypatch.setattr(moderation_module, "ADMIN_ROOM", "admin@conference.example.test")
    bot = await make_bot()
    bot.protected_rooms = {
        "one@conference.example.test",
        "two@conference.example.test",
    }
    started: set[str] = set()
    both_started = asyncio.Event()
    release_rooms = asyncio.Event()

    async def slow_apply_ban_to_room(room, *args, **kwargs):
        started.add(room)
        if len(started) == 2:
            both_started.set()
        await release_rooms.wait()

    bot.apply_ban_to_room = slow_apply_ban_to_room
    ban_task = asyncio.create_task(
        bot.ban_all(
            "user@example.org",
            None,
            issuer="admin@example.test",
            comment="spam",
            auto_redact=False,
        )
    )
    try:
        await asyncio.wait_for(both_started.wait(), timeout=1)
        assert started == bot.protected_rooms
        assert any("✅ Banned user@example.org" in msg["mbody"] for msg in bot.sent)
    finally:
        release_rooms.set()
        await asyncio.gather(ban_task, return_exceptions=True)
        await bot.db.close()


@pytest.mark.asyncio
async def test_slow_rtbl_publish_does_not_delay_ack_or_redaction_start(
    temp_db_path,
    monkeypatch,
):
    moderation_module = importlib.import_module("banbot.moderation")

    monkeypatch.setattr(moderation_module, "ADMIN_ROOM", "admin@conference.example.test")
    bot = await make_bot()
    rtbl_started = asyncio.Event()
    release_rtbl = asyncio.Event()
    redaction_started = asyncio.Event()

    async def slow_publish(jid=None, domain=None, comment=None):
        rtbl_started.set()
        await release_rtbl.wait()
        bot.published.append((jid, domain, comment))

    async def observe_redaction(jid, comment, actor=None):
        redaction_started.set()
        bot.auto_redactions.append((jid, comment, actor))

    bot.rtbl_publish_ban = slow_publish
    bot.maybe_auto_redact_after_ban = observe_redaction
    ban_task = asyncio.create_task(
        bot.ban_all(
            "user@example.org",
            None,
            issuer="admin@example.test",
            comment="spam",
        )
    )
    try:
        await asyncio.wait_for(rtbl_started.wait(), timeout=1)
        await asyncio.wait_for(redaction_started.wait(), timeout=1)

        assert any("✅ Banned user@example.org" in msg["mbody"] for msg in bot.sent)
        assert bot.applied_bans == [
            (
                "room@conference.example.test",
                "user@example.org",
                "nick",
                "spam",
                "admin@example.test",
            )
        ]
        assert ban_task.done() is False
    finally:
        release_rtbl.set()
        await asyncio.gather(ban_task, return_exceptions=True)
        tasks = list(getattr(bot, "redaction_operation_tasks", set()))
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await bot.db.close()


@pytest.mark.asyncio
async def test_permanent_ban_does_not_wait_for_command_auto_redaction(temp_db_path, monkeypatch):
    moderation_module = importlib.import_module("banbot.moderation")

    monkeypatch.setattr(moderation_module, "ADMIN_ROOM", "admin@conference.example.test")
    bot = await make_bot()
    release_redaction = asyncio.Event()
    redaction_started = asyncio.Event()

    async def slow_auto_redaction(jid, comment, actor=None):
        redaction_started.set()
        await release_redaction.wait()
        bot.auto_redactions.append((jid, comment, actor))

    bot.maybe_auto_redact_after_ban = slow_auto_redaction
    try:
        await asyncio.wait_for(
            bot.ban_all(
                "user@example.org",
                None,
                issuer="admin@example.test",
                comment="spam",
            ),
            timeout=1,
        )

        await asyncio.wait_for(redaction_started.wait(), timeout=1)
        assert bot.auto_redactions == []
        assert any("✅ Banned user@example.org" in msg["mbody"] for msg in bot.sent)

        release_redaction.set()
        tasks = list(bot.redaction_operation_tasks)
        if tasks:
            await asyncio.gather(*tasks)
        assert bot.auto_redactions == [("user@example.org", "spam", "admin@example.test")]
    finally:
        release_redaction.set()
        tasks = list(getattr(bot, "redaction_operation_tasks", set()))
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await bot.db.close()


@pytest.mark.asyncio
async def test_matching_auto_redaction_announces_background_start_before_worker(
    temp_db_path,
    monkeypatch,
):
    moderation_module = importlib.import_module("banbot.moderation")

    monkeypatch.setattr(
        moderation_module,
        "ADMIN_ROOM",
        "admin@conference.example.test",
    )
    bot = await make_bot()
    order: list[str] = []
    original_send = bot.bot_send_message

    async def observe_send(**kwargs):
        if kwargs.get("mbody", "").startswith("🧹 Auto-redaction started"):
            order.append("announcement")
        await original_send(**kwargs)

    async def observe_redaction(jid, comment, actor=None):
        order.append("worker")
        bot.auto_redactions.append((jid, comment, actor))

    bot.bot_send_message = observe_send
    bot._redaction_auto_reason_matches = lambda comment: "spam"
    bot.maybe_auto_redact_after_ban = observe_redaction
    try:
        await bot.ban_all(
            "user@example.org",
            None,
            issuer="admin@example.test",
            comment="spam",
        )

        tasks = list(bot.redaction_operation_tasks)
        if tasks:
            await asyncio.gather(*tasks)

        bodies = [message["mbody"] for message in bot.sent]
        assert bodies.index(
            "✅ Banned user@example.org (spam) by admin@example.test"
        ) < bodies.index(
            "🧹 Auto-redaction started in the background for "
            "user@example.org. Results will follow."
        )
        assert order == ["announcement", "worker"]
        assert bot.auto_redactions == [
            ("user@example.org", "spam", "admin@example.test")
        ]
    finally:
        tasks = list(getattr(bot, "redaction_operation_tasks", set()))
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await bot.db.close()


@pytest.mark.asyncio
async def test_unmatched_ban_reason_does_not_announce_or_schedule_auto_redaction(
    temp_db_path,
    monkeypatch,
):
    moderation_module = importlib.import_module("banbot.moderation")

    monkeypatch.setattr(
        moderation_module,
        "ADMIN_ROOM",
        "admin@conference.example.test",
    )
    bot = await make_bot()
    bot._redaction_auto_reason_matches = lambda comment: None
    try:
        await bot.ban_all(
            "user@example.org",
            None,
            issuer="admin@example.test",
            comment="policy violation",
        )

        assert bot.auto_redactions == []
        assert not getattr(bot, "redaction_operation_tasks", set())
        assert not any(
            "Auto-redaction started" in message["mbody"]
            for message in bot.sent
        )
    finally:
        await bot.db.close()


@pytest.mark.asyncio
async def test_tempban_does_not_run_command_auto_redaction(temp_db_path, monkeypatch):
    moderation_module = importlib.import_module("banbot.moderation")

    monkeypatch.setattr(moderation_module, "ADMIN_ROOM", "admin@conference.example.test")
    bot = await make_bot()
    try:
        until_ts = int(time.time()) + 86400
        await bot.ban_all(
            "user@example.org",
            until_ts,
            issuer="admin@example.test",
            comment="spam",
        )

        assert bot.auto_redactions == []
        assert bot.retracted == [("user@example.org", None)]
        async with bot.db.execute(
            "SELECT until FROM bans WHERE jid = ?",
            ("user@example.org",),
        ) as cursor:
            row = await cursor.fetchone()
        assert row is not None
        assert row[0] == until_ts
    finally:
        await bot.db.close()


@pytest.mark.asyncio
async def test_ban_all_refuses_admin_protected_target(temp_db_path, monkeypatch):
    moderation_module = importlib.import_module("banbot.moderation")

    monkeypatch.setattr(moderation_module, "ADMIN_ROOM", "admin@conference.example.test")
    bot = await make_bot()
    bot.protect_result = (True, "user@example.org is admin/owner")
    try:
        await bot.ban_all("user@example.org", None, issuer="admin@example.test", comment="spam")

        assert bot.applied_bans == []
        assert bot.published == []
        async with bot.db.execute("SELECT COUNT(*) FROM bans") as cursor:
            assert (await cursor.fetchone())[0] == 0
        assert "Refusing ban" in bot.sent[-1]["mbody"]
    finally:
        await bot.db.close()


@pytest.mark.asyncio
async def test_unban_all_removes_db_cache_applies_unban_and_retracts(temp_db_path, monkeypatch):
    moderation_module = importlib.import_module("banbot.moderation")

    monkeypatch.setattr(moderation_module, "ADMIN_ROOM", "admin@conference.example.test")
    bot = await make_bot()
    try:
        await bot.upsert_ban_db("user@example.org", "nick", 0, "admin@example.test", "spam")
        await bot.load_bans_from_db()

        await bot.unban_all("user@example.org", issuer="admin@example.test")

        assert "user@example.org" not in bot.ban_index_by_jid
        assert bot.applied_unbans == [("room@conference.example.test", "user@example.org", "nick", None, False)]
        assert bot.retracted == [("user@example.org", None)]
        assert any("♻️ Unbanned user@example.org" in msg["mbody"] for msg in bot.sent)
    finally:
        await bot.db.close()


@pytest.mark.asyncio
async def test_tempban_duration_limit_is_enforced(temp_db_path, monkeypatch):
    moderation_module = importlib.import_module("banbot.moderation")

    monkeypatch.setattr(moderation_module, "ADMIN_ROOM", "admin@conference.example.test")
    bot = await make_bot()
    bot.max_tempban_days = 1
    try:
        await bot.ban_all("user@example.org", int(time.time()) + 3 * 86400, issuer="admin@example.test")

        assert bot.applied_bans == []
        assert "MAX_TEMPBAN_DAYS" in bot.sent[-1]["mbody"]
    finally:
        await bot.db.close()

@pytest.mark.asyncio
async def test_repeated_permanent_ban_updates_reason_only(temp_db_path, monkeypatch):
    moderation_module = importlib.import_module("banbot.moderation")
    monkeypatch.setattr(moderation_module, "ADMIN_ROOM", "admin@conference.example.test")
    bot = await make_bot()
    try:
        await bot.ban_all("user@example.org", None, issuer="first-admin@example.test", comment="old reason")
        bot.sent.clear()
        bot.audit_events.clear()

        await bot.ban_all("user@example.org", None, issuer="second-admin@example.test", comment="new reason")

        async with bot.db.execute(
            "SELECT until, issuer, comment FROM bans WHERE target_type = 'jid' AND target = ?",
            ("user@example.org",),
        ) as cursor:
            row = await cursor.fetchone()
        assert row == (0, "first-admin@example.test", "new reason")
        assert any("Ban reason updated" in msg["mbody"] for msg in bot.sent)
        assert bot.audit_events[-1][0] == "ban_updated"
        assert bot.audit_events[-1][1]["actor"] == "second-admin@example.test"
        assert bot.audit_events[-1][1]["details"]["old_comment"] == "old reason"
        assert bot.audit_events[-1][1]["details"]["new_comment"] == "new reason"
    finally:
        await bot.db.close()


@pytest.mark.asyncio
async def test_repeated_permanent_ban_without_reason_remains_duplicate(temp_db_path, monkeypatch):
    moderation_module = importlib.import_module("banbot.moderation")
    monkeypatch.setattr(moderation_module, "ADMIN_ROOM", "admin@conference.example.test")
    bot = await make_bot()
    try:
        await bot.ban_all("user@example.org", None, issuer="first-admin@example.test", comment="keep me")
        bot.sent.clear()

        await bot.ban_all("user@example.org", None, issuer="second-admin@example.test")

        async with bot.db.execute(
            "SELECT issuer, comment FROM bans WHERE target_type = 'jid' AND target = ?",
            ("user@example.org",),
        ) as cursor:
            row = await cursor.fetchone()
        assert row == ("first-admin@example.test", "keep me")
        assert any("Ban already exists" in msg["mbody"] for msg in bot.sent)
    finally:
        await bot.db.close()


@pytest.mark.asyncio
async def test_repeated_tempban_without_reason_preserves_existing_reason(temp_db_path, monkeypatch):
    moderation_module = importlib.import_module("banbot.moderation")
    monkeypatch.setattr(moderation_module, "ADMIN_ROOM", "admin@conference.example.test")
    bot = await make_bot()
    try:
        first_until = int(time.time()) + 3600
        second_until = int(time.time()) + 7200
        await bot.ban_all("user@example.org", first_until, issuer="first-admin@example.test", comment="keep me")
        bot.sent.clear()

        await bot.ban_all("user@example.org", second_until, issuer="second-admin@example.test")

        async with bot.db.execute(
            "SELECT until, issuer, comment FROM bans WHERE target_type = 'jid' AND target = ?",
            ("user@example.org",),
        ) as cursor:
            row = await cursor.fetchone()
        assert row == (second_until, "second-admin@example.test", "keep me")
        assert any("tempban duration changed" in msg["mbody"] for msg in bot.sent)
    finally:
        await bot.db.close()

@pytest.mark.asyncio
async def test_ban_all_strips_admin_resource_from_storage_and_confirmation(temp_db_path, monkeypatch):
    moderation_module = importlib.import_module("banbot.moderation")
    monkeypatch.setattr(moderation_module, "ADMIN_ROOM", "admin@conference.example.test")
    bot = await make_bot()
    try:
        await bot.ban_all(
            "user@example.org",
            None,
            issuer="admin@example.test/gajim.A0Q0E069",
            comment="spam",
        )

        async with bot.db.execute(
            "SELECT issuer FROM bans WHERE jid = ?", ("user@example.org",)
        ) as cursor:
            row = await cursor.fetchone()
        assert row == ("admin@example.test",)
        assert any("by admin@example.test" in msg["mbody"] for msg in bot.sent)
        assert all("gajim.A0Q0E069" not in msg["mbody"] for msg in bot.sent)
    finally:
        await bot.db.close()


@pytest.mark.asyncio
async def test_unban_worker_suppresses_policy_notice_for_expired_tempban(temp_db_path):
    bot = await make_bot()
    try:
        await bot.upsert_ban_db(
            "expired@example.org", "Expired", int(time.time()) - 1,
            "admin@example.test", "spam"
        )
        calls = []

        async def capture_unban(identifier, issuer=None, *, notify_policy=True):
            calls.append((identifier, issuer, notify_policy))
            raise asyncio.CancelledError

        bot.unban_all = capture_unban
        with pytest.raises(asyncio.CancelledError):
            await bot.unban_worker()

        assert calls == [("expired@example.org", "system", False)]
    finally:
        await bot.db.close()


@pytest.mark.asyncio
async def test_expired_tempban_is_retained_if_room_unban_fails(temp_db_path):
    bot = await make_bot()
    try:
        until = int(time.time()) - 1
        await bot.upsert_ban_db(
            "expired@example.org",
            "Expired",
            until,
            "admin@example.test",
            "spam",
        )

        async def failed_room_unban(*_args, **_kwargs):
            return False

        bot.apply_unban_to_room = failed_room_unban
        removed = await bot.unban_all(
            "expired@example.org",
            issuer="system",
            notify_policy=False,
        )

        assert removed is False
        async with bot.db.execute(
            "SELECT until FROM bans WHERE target_type='jid' AND target=?",
            ("expired@example.org",),
        ) as cursor:
            row = await cursor.fetchone()
        assert row == (until,)
    finally:
        await bot.db.close()


@pytest.mark.asyncio
async def test_expired_tempban_unban_is_deferred_while_reconnecting(temp_db_path):
    bot = await make_bot()
    try:
        until = int(time.time()) - 1
        await bot.upsert_ban_db(
            "expired@example.org",
            "Expired",
            until,
            "admin@example.test",
            "spam",
        )
        bot.reconnecting = True

        removed = await bot.unban_all(
            "expired@example.org",
            issuer="system",
            notify_policy=False,
        )

        assert removed is False
        assert bot.applied_unbans == []
        async with bot.db.execute(
            "SELECT until FROM bans WHERE target_type='jid' AND target=?",
            ("expired@example.org",),
        ) as cursor:
            row = await cursor.fetchone()
        assert row == (until,)
    finally:
        await bot.db.close()


@pytest.mark.asyncio
async def test_unban_worker_keeps_expired_rows_while_disconnected(temp_db_path, monkeypatch):
    bot = await make_bot()
    try:
        until = int(time.time()) - 1
        await bot.upsert_ban_db(
            "expired@example.org",
            "Expired",
            until,
            "admin@example.test",
            "spam",
        )
        bot.reconnecting = True
        bot.unban_check_interval = 0
        sleeps = 0

        async def stop_after_one_skip(_delay):
            nonlocal sleeps
            sleeps += 1
            raise asyncio.CancelledError

        moderation_module = importlib.import_module("banbot.moderation")
        monkeypatch.setattr(moderation_module.asyncio, "sleep", stop_after_one_skip)

        with pytest.raises(asyncio.CancelledError):
            await bot.unban_worker()

        assert sleeps == 1
        assert bot.applied_unbans == []
        async with bot.db.execute(
            "SELECT until FROM bans WHERE target_type='jid' AND target=?",
            ("expired@example.org",),
        ) as cursor:
            row = await cursor.fetchone()
        assert row == (until,)
    finally:
        await bot.db.close()


@pytest.mark.asyncio
async def test_manual_unban_retains_ban_when_any_room_unban_fails(temp_db_path, monkeypatch):
    moderation_module = importlib.import_module("banbot.moderation")
    monkeypatch.setattr(moderation_module, "ADMIN_ROOM", "admin@conference.example.test")

    bot = await make_bot()
    bot.protected_rooms = {
        "one@conference.example.test",
        "two@conference.example.test",
    }
    try:
        await bot.upsert_ban_db(
            "user@example.org",
            "User",
            0,
            "admin@example.test",
            "spam",
        )
        await bot.load_bans_from_db()

        calls = []

        async def partial_unban(room, ban_jid, ban_nick, domain=None, *, announce=True):
            calls.append((room, ban_jid, ban_nick, domain, announce))
            return room != "two@conference.example.test"

        bot.apply_unban_to_room = partial_unban

        removed = await bot.unban_all("user@example.org", issuer="admin@example.test")

        assert removed is False
        assert {call[0] for call in calls} == bot.protected_rooms
        assert all(call[-1] is False for call in calls)
        async with bot.db.execute(
            "SELECT target FROM bans WHERE target_type='jid' AND target=?",
            ("user@example.org",),
        ) as cursor:
            assert await cursor.fetchone() == ("user@example.org",)
        assert bot.retracted == []
        assert "Unban not completed" in bot.sent[-1]["mbody"]
        assert not any(message["mbody"].startswith("♻️ Unbanned") for message in bot.sent)
    finally:
        await bot.db.close()


@pytest.mark.asyncio
async def test_unban_dotted_nick_falls_back_when_no_domain_ban_exists(temp_db_path, monkeypatch):
    moderation_module = importlib.import_module("banbot.moderation")
    monkeypatch.setattr(moderation_module, "ADMIN_ROOM", "admin@conference.example.test")

    bot = await make_bot()
    bot.allow_user_cmds = False
    try:
        await bot.upsert_ban_db(
            None,
            "john.doe",
            0,
            "admin@example.test",
            "nick spam",
        )
        await bot.load_bans_from_db()

        removed = await bot.unban_all("john.doe", issuer="admin@example.test")

        assert removed is True
        async with bot.db.execute(
            "SELECT 1 FROM bans WHERE target_type='nick' AND target='john.doe'"
        ) as cursor:
            assert await cursor.fetchone() is None
        assert bot.applied_unbans == [
            ("room@conference.example.test", None, "john.doe", None, False)
        ]
    finally:
        await bot.db.close()


@pytest.mark.asyncio
async def test_unban_plain_domain_prefers_existing_domain_over_same_named_nick(temp_db_path, monkeypatch):
    moderation_module = importlib.import_module("banbot.moderation")
    monkeypatch.setattr(moderation_module, "ADMIN_ROOM", "admin@conference.example.test")

    bot = await make_bot()
    bot.allow_user_cmds = False
    try:
        await bot.upsert_ban_db(
            "*.example.org",
            None,
            0,
            "admin@example.test",
            "domain spam",
        )
        await bot.upsert_ban_db(
            None,
            "example.org",
            0,
            "admin@example.test",
            "same-named nick",
        )
        await bot.load_bans_from_db()

        removed = await bot.unban_all("example.org", issuer="admin@example.test")

        assert removed is True
        async with bot.db.execute(
            "SELECT target_type FROM bans WHERE target='example.org' ORDER BY target_type"
        ) as cursor:
            assert await cursor.fetchall() == [("nick",)]
        assert bot.applied_unbans == [
            ("room@conference.example.test", None, None, "example.org", False)
        ]
    finally:
        await bot.db.close()
