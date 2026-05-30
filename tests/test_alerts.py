"""Operational alert helper tests."""

from __future__ import annotations

import pytest

from banbot.alerts import AlertMixin


class AlertBot(AlertMixin):
    def __init__(self):
        self.init_alert_state()
        self.sent = []
        self.audited = []
        self.alert_dedup_window = 300
        self.fail_send = False
        self.fail_audit = False

    async def bot_send_message(self, **kwargs):
        if self.fail_send:
            raise RuntimeError("send failed")
        self.sent.append(kwargs)

    async def audit_event(self, event_type, **kwargs):
        if self.fail_audit:
            raise RuntimeError("audit failed")
        self.audited.append((event_type, kwargs))


@pytest.mark.asyncio
async def test_operational_alert_sends_and_audits(monkeypatch):
    bot = AlertBot()
    monkeypatch.setattr("banbot.alerts.time.time", lambda: 1000.0)

    sent = await bot.send_operational_alert(
        "health:test",
        "Health warning",
        "Bot not in room room@example.org",
        details={"room": "room@example.org"},
    )

    assert sent is True
    assert bot.sent[-1]["mtype"] == "groupchat"
    assert bot.sent[-1]["mbody"] == "⚠️ Health warning\nBot not in room room@example.org"
    assert bot.audited[-1][0] == "operational_alert"
    assert bot.audited[-1][1]["details"]["key"] == "health:test"
    assert bot.audited[-1][1]["details"]["room"] == "room@example.org"


@pytest.mark.asyncio
async def test_operational_alert_disabled_and_deduplicated(monkeypatch):
    bot = AlertBot()

    assert await bot.send_operational_alert("k", "Title", "Msg", enabled=False) is False
    assert bot.sent == []

    monkeypatch.setattr("banbot.alerts.time.time", lambda: 1000.0)
    assert await bot.send_operational_alert("k", "Title", "First") is True
    assert len(bot.sent) == 1

    monkeypatch.setattr("banbot.alerts.time.time", lambda: 1200.0)
    assert await bot.send_operational_alert("k", "Title", "Suppressed") is False
    assert len(bot.sent) == 1

    monkeypatch.setattr("banbot.alerts.time.time", lambda: 1301.0)
    assert await bot.send_operational_alert("k", "Title", "After window") is True
    assert len(bot.sent) == 2
    assert "After window" in bot.sent[-1]["mbody"]


@pytest.mark.asyncio
async def test_operational_alert_handles_send_and_audit_failures(monkeypatch):
    bot = AlertBot()
    monkeypatch.setattr("banbot.alerts.time.time", lambda: 1000.0)

    bot.fail_send = True
    assert await bot.send_operational_alert("send", "Title", "Msg") is False
    assert bot.audited == []

    bot = AlertBot()
    bot.fail_audit = True
    assert await bot.send_operational_alert("audit", "Title", "Msg") is True
    assert len(bot.sent) == 1
    assert bot.audited == []


@pytest.mark.asyncio
async def test_record_alert_failure_threshold_and_success_reset(monkeypatch):
    bot = AlertBot()
    monkeypatch.setattr("banbot.alerts.time.time", lambda: 1000.0)

    assert await bot.record_alert_failure("rtbl", "RTBL failed", "Refresh failed", threshold=3) is False
    assert await bot.record_alert_failure("rtbl", "RTBL failed", "Refresh failed", threshold=3) is False
    assert bot.sent == []

    assert await bot.record_alert_failure("rtbl", "RTBL failed", "Refresh failed", threshold=3) is True
    assert "Consecutive failures: 3" in bot.sent[-1]["mbody"]

    bot.record_alert_success("rtbl")
    assert "rtbl" not in bot.alert_counters
