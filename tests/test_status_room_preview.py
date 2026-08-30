"""Dependency-light coverage for protected-room output in ``!status``."""

from __future__ import annotations

import importlib
import time
from types import SimpleNamespace

import pytest

from banbot.status import StatusMixin


class StatusRoomPreviewBot(StatusMixin):
    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.command_prefix = "!"
        self.protected_rooms = {
            f"room{index:02d}@conference.example.test" for index in range(12)
        }
        self.bot_admin_state = {room: True for room in self.protected_rooms}
        self.room_bot_nicks = {
            "room00@conference.example.test": "EffectiveBot",
            "room01@conference.example.test": "EffectiveBot",
        }
        self.occupants = {
            "admin@conference.example.org": {
                "Admin": {
                    "jid": "admin@example.org/resource",
                    "affiliation": "owner",
                }
            },
            "room00@conference.example.test": {
                "EffectiveBot": {
                    "jid": "bot@example.org/resource",
                    "affiliation": "owner",
                }
            },
            "room01@conference.example.test": {
                "EffectiveBot": {
                    "jid": "bot@example.org/resource",
                    "affiliation": "member",
                }
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


@pytest.mark.asyncio
async def test_status_reuses_room_list_lines_and_keeps_ten_room_preview(monkeypatch):
    bot = StatusRoomPreviewBot()
    status_module = importlib.import_module("banbot.status")

    class FakeProcess:
        def memory_info(self):
            return type("Mem", (), {"rss": 42 * 1024 * 1024})()

        def cpu_percent(self, interval):
            return 0.5

    monkeypatch.setattr(status_module.psutil, "Process", lambda pid: FakeProcess())
    monkeypatch.setattr(status_module.psutil, "getloadavg", lambda: (0.1, 0.2, 0.3))
    monkeypatch.setattr(status_module.psutil, "cpu_count", lambda: 8)

    await bot._cmd_status("admin@conference.example.org")
    body = bot.sent[-1]["mbody"]

    assert (
        "🟢 room00@conference.example.test | joined | bot affiliation: owner"
        in body
    )
    assert (
        "🟠 room01@conference.example.test | joined | "
        "bot affiliation: member (no admin rights)"
        in body
    )
    assert (
        "🔴 room02@conference.example.test | not joined | "
        "bot affiliation: unknown"
        in body
    )
    assert "room09@conference.example.test" in body
    assert "room10@conference.example.test" not in body
    assert "room11@conference.example.test" not in body
    assert "... and 2 more." in body
    assert "Use !room list [page] to view all protected rooms." in body


@pytest.mark.asyncio
async def test_status_reports_current_worker_restart_backoff_without_duplicate_history(monkeypatch):
    bot = StatusRoomPreviewBot()
    bot.tasks = SimpleNamespace(
        snapshot=lambda include_done=False: [
            SimpleNamespace(
                name="unban-worker",
                status="restarting",
                restart_count=1,
            )
        ]
    )
    status_module = importlib.import_module("banbot.status")

    class FakeProcess:
        def memory_info(self):
            return type("Mem", (), {"rss": 42 * 1024 * 1024})()

        def cpu_percent(self, interval):
            return 0.5

    monkeypatch.setattr(status_module.psutil, "Process", lambda pid: FakeProcess())
    monkeypatch.setattr(status_module.psutil, "getloadavg", lambda: (0.1, 0.2, 0.3))
    monkeypatch.setattr(status_module.psutil, "cpu_count", lambda: 8)

    await bot._cmd_status("admin@conference.example.org")
    body = bot.sent[-1]["mbody"]

    assert "Background worker restart/backoff in progress: unban-worker" in body
    assert "Background worker restart(s) observed: unban-worker×1" not in body
