from __future__ import annotations

import json
import logging

import pytest

from banbot.protections import ProtectionMixin


class FakeCursor:
    def __init__(self, rows):
        self.rows = rows

    async def fetchall(self):
        return list(self.rows)


class FakeExecute:
    def __init__(self, db: "FakeDb", sql: str, params=None) -> None:
        self.db = db
        self.sql = sql
        self.params = params
        self.result = None

    def __await__(self):
        async def _run():
            self.result = self.db._execute_now(self.sql, self.params)
            return self.result

        return _run().__await__()

    async def __aenter__(self):
        self.result = self.db._execute_now(self.sql, self.params)
        return self.result

    async def __aexit__(self, exc_type, exc, tb):
        return False


class FakeDb:
    def __init__(self, rows=None) -> None:
        self.rows = list(rows or [])
        self.executed: list[tuple[str, object]] = []
        self.commits = 0
        self.persisted: dict[str, tuple[int, str, int]] = {}

    def execute(self, sql: str, params=None) -> FakeExecute:
        return FakeExecute(self, sql, params)

    def _execute_now(self, sql: str, params=None):
        self.executed.append((" ".join(sql.split()), params))
        normalized = sql.strip().upper()
        if normalized.startswith("SELECT"):
            return FakeCursor(self.rows)
        if normalized.startswith("INSERT INTO PROTECTIONS"):
            assert params is not None
            name, enabled, config_json, updated_at = params
            self.persisted[str(name)] = (int(enabled), str(config_json), int(updated_at))
        return None

    async def commit(self) -> None:
        self.commits += 1


class DummyStorage(ProtectionMixin):
    def __init__(self, db=None) -> None:
        self.db = db
        self.init_protection_state()


def test_load_protections_without_db_initializes_defaults() -> None:
    bot = DummyStorage(db=None)
    bot.protections.clear()

    async def run() -> None:
        await bot.load_protections()

    import asyncio

    asyncio.run(run())

    assert "FloodSpamProtection" in bot.protections
    assert bot.protections["FloodSpamProtection"]["enabled"] is False


@pytest.mark.asyncio
async def test_setup_protections_db_creates_table_and_commits() -> None:
    db = FakeDb()
    bot = DummyStorage(db=db)

    await bot.setup_protections_db()

    assert any("CREATE TABLE IF NOT EXISTS protections" in sql for sql, _ in db.executed)
    assert db.commits == 1


@pytest.mark.asyncio
async def test_persist_protection_stores_enabled_separately_from_config_json(monkeypatch) -> None:
    db = FakeDb()
    bot = DummyStorage(db=db)
    bot.protections["FloodSpamProtection"].update({
        "enabled": True,
        "max_messages": 3,
        "action": "ban",
    })
    monkeypatch.setattr("banbot.protections.storage.time.time", lambda: 1234.9)

    await bot.persist_protection("FloodSpamProtection")

    enabled, config_json, updated_at = db.persisted["FloodSpamProtection"]
    loaded = json.loads(config_json)
    assert enabled == 1
    assert updated_at == 1234
    assert "enabled" not in loaded
    assert loaded["max_messages"] == 3
    assert loaded["action"] == "ban"
    assert db.commits == 2  # table setup + upsert


@pytest.mark.asyncio
async def test_load_protections_merges_persisted_alias_rows_and_ignores_unknowns(caplog) -> None:
    db = FakeDb(rows=[
        ("flood", 1, '{"max_messages": 2, "action": "notify"}'),
        ("UnknownProtection", 1, '{"enabled": true}'),
    ])
    bot = DummyStorage(db=db)
    bot.protections["FloodSpamProtection"]["max_messages"] = 99

    with caplog.at_level(logging.WARNING, logger="banbot.protections.storage"):
        await bot.load_protections()

    assert bot.protections["FloodSpamProtection"]["enabled"] is True
    assert bot.protections["FloodSpamProtection"]["max_messages"] == 2
    assert bot.protections["FloodSpamProtection"]["action"] == "notify"
    assert "UnknownProtection" not in bot.protections
    assert "Ignoring unknown persisted protection" in caplog.text


@pytest.mark.asyncio
async def test_load_protections_ignores_invalid_or_non_dict_config_json() -> None:
    db = FakeDb(rows=[
        ("mentions", 1, "not-json"),
        ("policy", 0, '["not", "a", "dict"]'),
    ])
    bot = DummyStorage(db=db)

    await bot.load_protections()

    assert bot.protections["MentionLimitProtection"]["enabled"] is True
    assert bot.protections["MentionLimitProtection"]["max_mentions"] == 5
    assert bot.protections["PolicyChangeNotification"]["enabled"] is False
    assert bot.protections["PolicyChangeNotification"]["notify_bans"] is True
