"""Focused tests for automatic MUC health recovery."""

from __future__ import annotations

import asyncio

import pytest

from banbot.health_check import HealthCheckMixin


ROOM = "room@conference.example.test"


class RecoveryBot(HealthCheckMixin):
    def __init__(self) -> None:
        self.occupants = {ROOM: {}}
        self.bot_admin_state = {}
        self.sent: list[dict] = []
        self.synced: list[str] = []
        self.alert_on_health_check_failure = True
        self.alert_on_admin_rights_lost = True

    async def bot_send_message(self, **kwargs) -> None:
        self.sent.append(kwargs)

    async def ensure_muc_joined(self, room: str, *, force: bool = False) -> bool:
        assert room == ROOM
        assert force is True
        self.occupants[room] = {
            "BanBot": {
                "jid": "bot@example.org",
                "affiliation": "admin",
                "role": "moderator",
            }
        }
        return True

    async def sync_bans_to_rooms_for_single_room(self, room: str) -> None:
        self.synced.append(room)

    def is_bot_admin_or_owner(self, room: str) -> bool:
        return self.occupants.get(room, {}).get("BanBot", {}).get("affiliation") in {
            "owner",
            "admin",
        }


@pytest.mark.asyncio
async def test_health_rejoin_resyncs_bans_and_announces_recovery() -> None:
    bot = RecoveryBot()

    await bot._health_check_room(ROOM)

    assert bot.bot_admin_state[ROOM] is True
    assert bot.synced == [ROOM]
    assert bot.sent[-1]["mbody"] == (
        f"✅ Automatic room rejoin succeeded for {ROOM}. Active bans were resynced."
    )


@pytest.mark.asyncio
async def test_health_worker_runs_first_cycle_before_initial_sleep(monkeypatch) -> None:
    bot = RecoveryBot()
    bot.health_check_interval = 300
    calls: list[str] = []

    async def cycle() -> None:
        calls.append("cycle")

    async def stop_on_sleep(delay: float) -> None:
        calls.append(f"sleep:{delay:g}")
        raise asyncio.CancelledError()

    bot._run_health_check_cycle = cycle
    monkeypatch.setattr("banbot.health_check.asyncio.sleep", stop_on_sleep)

    with pytest.raises(asyncio.CancelledError):
        await bot.health_check_worker()

    assert calls == ["cycle", "sleep:300"]
