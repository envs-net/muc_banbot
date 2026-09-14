"""Dependency-light coverage for compact/full protected-room status output."""

from __future__ import annotations

import importlib
import time
from types import SimpleNamespace

import pytest
from envs_xmpp_core.runtime import SessionLifecycleSnapshot

from banbot.status import StatusMixin


class StatusRoomPreviewBot(StatusMixin):
    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.command_prefix = "!"
        self.protected_rooms = {f"room{index:02d}@conference.example.test" for index in range(12)}
        self.bot_admin_state = {room: True for room in self.protected_rooms}
        self.room_bot_nicks = {
            "room00@conference.example.test": "EffectiveBot",
            "room01@conference.example.test": "EffectiveBot",
        }
        self.occupants = {
            "admin@conference.example.org": {
                "Admin": {"jid": "admin@example.org/resource", "affiliation": "owner"}
            },
            "room00@conference.example.test": {
                "EffectiveBot": {"jid": "bot@example.org/resource", "affiliation": "owner"}
            },
            "room01@conference.example.test": {
                "EffectiveBot": {"jid": "bot@example.org/resource", "affiliation": "member"}
            },
        }
        self.db = None
        self.reconnecting = False
        self.last_reconnect_time = None
        self.last_version_check_result = None
        self.bot_start_time = int(time.time()) - 60
        self.server_connect_time = None
        self.audit_log_retention_days = 30
        self.admin_affiliation_query_forbidden_rooms = set()
        self.rtbl_enabled = False
        self.rtbl_publish_enabled = False
        self.rtbl_publish_config_enabled = False
        self.pending_room_invites = {}
        self.protections = {}
        self.redaction_enabled = False
        self.tasks = None

    async def get_db_stats(self) -> dict:
        return {
            "db_size_bytes": 0,
            "audit_events": 0,
            "permanent_bans": 0,
            "temporary_bans": 0,
            "expired_ban_rows": 0,
        }

    async def bot_send_message(self, **kwargs) -> None:
        self.sent.append(kwargs)

    @staticmethod
    def bare_jid(jid: str) -> str:
        return str(jid).split("/", 1)[0].lower()

    @staticmethod
    def safe_jid(jid: str) -> str:
        return str(jid).split("/", 1)[0]


def _patch_process(monkeypatch):
    status_module = importlib.import_module("banbot.status")

    class FakeProcess:
        def memory_info(self):
            return type("Mem", (), {"rss": 42 * 1024 * 1024})()

        def cpu_percent(self, interval):
            return 0.5

    monkeypatch.setattr(status_module.psutil, "Process", lambda pid: FakeProcess())
    monkeypatch.setattr(status_module.psutil, "getloadavg", lambda: (0.1, 0.2, 0.3))
    monkeypatch.setattr(status_module.psutil, "cpu_count", lambda: 8)


@pytest.mark.asyncio
async def test_status_compact_summarizes_rooms_without_inventory(monkeypatch):
    bot = StatusRoomPreviewBot()
    _patch_process(monkeypatch)

    await bot._cmd_status("admin@conference.example.org")
    body = bot.sent[-1]["mbody"]

    assert body.startswith("🤖 muc_banbot Status")
    assert "⚙️ Core:" in body
    assert "🖥️ Runtime:" in body
    assert "💬 XMPP:" in body
    assert "Summary: 12 configured · 2 joined · 11 issues" in body
    assert "room00@conference.example.test" not in body
    assert "🛡️ Moderation:" in body
    assert "🩺 Health:" in body
    assert "🛡️ Protections:" not in body


@pytest.mark.asyncio
async def test_status_full_adds_bounded_problem_room_diagnostics(monkeypatch):
    bot = StatusRoomPreviewBot()
    _patch_process(monkeypatch)

    await bot._cmd_status("admin@conference.example.org", ["full"])
    body = bot.sent[-1]["mbody"]

    assert "🏠 Room issues:" in body
    assert "room00@conference.example.test" not in body
    assert "room01@conference.example.test" in body
    assert "no admin rights" in body
    assert "… 1 more; see !room list problems all" in body
    assert "🛡️ Protections:" in body
    assert "👥 Admins/Owners:" in body


@pytest.mark.asyncio
async def test_status_reports_current_worker_restart_backoff(monkeypatch):
    bot = StatusRoomPreviewBot()
    bot.tasks = SimpleNamespace(
        snapshot=lambda include_done=False: [
            SimpleNamespace(
                group="_core",
                name="unban-worker",
                status="restarting",
                kind="service",
                created_at=0.0,
                heartbeat_at=None,
                restart_count=1,
                restart_at=None,
                last_error=None,
            )
        ]
    )
    _patch_process(monkeypatch)

    await bot._cmd_status("admin@conference.example.org")
    body = bot.sent[-1]["mbody"]

    assert "Background worker restart/backoff in progress: unban-worker" in body
    assert "Background worker restart(s) observed: unban-worker×1" not in body
    assert "Restarting: 1" in body


@pytest.mark.asyncio
async def test_status_exposes_shared_session_lifecycle_telemetry(monkeypatch):
    bot = StatusRoomPreviewBot()
    bot.session_lifecycle = SimpleNamespace(
        snapshot=lambda: SessionLifecycleSnapshot(
            generation=5,
            reconnect_count=4,
            state="starting",
            phase="synchronization",
            session_started_at="2026-09-14T05:00:00+00:00",
            last_ready_at="2026-09-14T04:59:00+00:00",
            last_disconnect_at="2026-09-14T04:59:55+00:00",
            last_disconnect_reason="connection lost",
            last_error=None,
            startup_duration_seconds=None,
            phase_age_seconds=7.0,
            session_age_seconds=7.0,
        )
    )
    _patch_process(monkeypatch)

    await bot._cmd_status("admin@conference.example.org", ["full"])
    body = bot.sent[-1]["mbody"]

    assert "Session: starting · generation 5 · reconnects 4" in body
    assert "Session phase: synchronization · age 7s" in body
    assert "Last disconnect: connection lost" in body


@pytest.mark.asyncio
async def test_status_shows_last_admin_sync_result(monkeypatch):
    bot = StatusRoomPreviewBot()
    bot.last_admin_sync_at = time.time() - 30
    bot.last_admin_sync_ok = False
    bot.last_admin_sync_error = "owner: IQ timeout after 10s"
    _patch_process(monkeypatch)

    await bot._cmd_status("admin@conference.example.org", ["full"])
    body = bot.sent[-1]["mbody"]

    assert "Admin sync: ⚠️ failed · 30s ago" in body
    assert "Admin sync error: owner: IQ timeout after 10s" in body
