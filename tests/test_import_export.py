import csv

import pytest

aiosqlite = pytest.importorskip("aiosqlite")

from banbot.cache import CacheMixin
from banbot.db import DatabaseMixin
from banbot.import_export import ImportExportMixin


class ImportBot(DatabaseMixin, CacheMixin, ImportExportMixin):
    def __init__(self):
        self.protected_rooms = set()
        self.ban_cache = {}
        self.ban_index_by_jid = {}
        self.ban_index_by_nick = {}
        self.ban_index_by_domain = {}
        self.events = []
        self.audit_events = []

    def log_event(self, level, event, **fields):
        self.events.append((level, event, fields))

    async def audit_event(self, event_type, **kwargs):
        self.audit_events.append((event_type, kwargs))


@pytest.mark.asyncio
async def test_export_bans_to_csv_uses_cache(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    bot = ImportBot()
    bot._cache_ban("User@Example.org/resource", "Nick", 0, "tester", "reason")

    ok, message = await bot.export_bans_to_csv()

    assert ok is True
    assert "Exported 1 bans" in message
    exports = list(tmp_path.glob("bans_export_*.csv"))
    assert len(exports) == 1
    with exports[0].open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert rows == [
        {
            "jid": "user@example.org",
            "nick": "nick",
            "until": "0",
            "issuer": "tester",
            "comment": "reason",
        }
    ]


@pytest.mark.asyncio
async def test_import_rejects_invalid_header(tmp_path):
    csv_file = tmp_path / "bad.csv"
    csv_file.write_text("jid,nick\nuser@example.org,Nick\n", encoding="utf-8")
    bot = ImportBot()

    successful, skipped, errors = await bot.import_bans_from_csv(str(csv_file))

    assert (successful, skipped) == (0, 0)
    assert "Invalid CSV header" in errors[0]


@pytest.mark.asyncio
async def test_import_bans_from_csv_creates_backup_and_upserts_rows(temp_db_path, tmp_path, monkeypatch):
    import banbot.import_export as import_export_module

    monkeypatch.setattr(import_export_module, "DB_FILE", str(temp_db_path), raising=False)
    csv_file = tmp_path / "bans.csv"
    csv_file.write_text(
        "jid,nick,until,issuer,comment\n"
        "User@Example.org/Device,Nick,0,imported,spam\n"
        "*.Example.org,,0,imported,domain spam\n",
        encoding="utf-8",
    )

    bot = ImportBot()
    await bot.setup_db()
    try:
        successful, skipped, errors = await bot.import_bans_from_csv(str(csv_file))

        assert (successful, skipped, errors) == (2, 0, [])
        assert bot.last_import_backup_file is not None
        assert temp_db_path.with_name(temp_db_path.name).exists()
        assert list(temp_db_path.parent.glob(temp_db_path.name + ".backup-before-import-*"))

        async with bot.db.execute("SELECT target_type, target, jid, nick, issuer, comment FROM bans ORDER BY target_type, target") as cursor:
            rows = await cursor.fetchall()
        assert ("domain", "example.org", "*.example.org", None, "imported", "domain spam") in rows
        assert ("jid", "user@example.org", "user@example.org", "nick", "imported", "spam") in rows
    finally:
        await bot.db.close()


@pytest.mark.asyncio
async def test_import_skips_invalid_rows_without_backup(tmp_path):
    csv_file = tmp_path / "invalid-rows.csv"
    csv_file.write_text(
        "jid,nick,until,issuer,comment\n"
        ",,0,imported,no target\n"
        "badjid,,0,imported,bad jid\n"
        "user@example.org,,not-a-number,imported,bad until\n",
        encoding="utf-8",
    )
    bot = ImportBot()

    successful, skipped, errors = await bot.import_bans_from_csv(str(csv_file))

    assert successful == 0
    assert skipped == 3
    assert len(errors) == 3
