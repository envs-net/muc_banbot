import asyncio
import csv
import pathlib

import pytest

aiosqlite = pytest.importorskip("aiosqlite")

from banbot.cache import CacheMixin
from banbot.db import DatabaseMixin
from banbot.import_export import ImportExportMixin
from banbot.backups import BackupMixin


class ImportBot(DatabaseMixin, CacheMixin, BackupMixin, ImportExportMixin):
    def __init__(self):
        self.protected_rooms = set()
        self.ban_cache = {}
        self.ban_index_by_jid = {}
        self.ban_index_by_nick = {}
        self.ban_index_by_domain = {}
        self.events = []
        self.audit_events = []
        self.last_database_backup_file = None
        self.last_database_restore_file = None
        self._pending_database_backup_audit_events = []
        self.sent = []
        self.command_prefix = "!"
        self.list_page_size = 10
        self._database_file_operation_lock = asyncio.Lock()
        self._ban_state_operation_lock = asyncio.Lock()

    def log_event(self, level, event, **fields):
        self.events.append((level, event, fields))

    async def audit_event(self, event_type, **kwargs):
        self.audit_events.append((event_type, kwargs))

    def bare_jid(self, jid: str | None) -> str | None:
        return jid.split("/")[0].lower() if jid else None

    async def bot_send_message(self, **kwargs):
        self.sent.append(kwargs)


@pytest.mark.asyncio
async def test_export_bans_to_csv_uses_cache(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    bot = ImportBot()
    bot._cache_ban("User@Example.org/resource", "Nick", 0, "tester", "reason")

    ok, message = await bot.export_bans_to_csv()

    assert ok is True
    assert "Exported 1 bans" in message
    exports = list((tmp_path / "data" / "exports").glob("bans_export_*.csv"))
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
    import banbot.backups as backups_module

    monkeypatch.setattr(backups_module.config, "DB_BACKUP_DIR", str(tmp_path / "backups"), raising=False)
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
        assert bot.last_database_backup_file is not None
        assert "snapshot-before-import" in bot.last_database_backup_file
        assert pathlib.Path(bot.last_database_backup_file).is_file()

        async with bot.db.execute("SELECT target_type, target, jid, nick, issuer, comment FROM bans ORDER BY target_type, target") as cursor:
            rows = await cursor.fetchall()
        assert ("domain", "example.org", "*.example.org", None, "imported", "domain spam") in rows
        assert ("jid", "user@example.org", "user@example.org", "nick", "imported", "spam") in rows
    finally:
        await bot.db.close()


@pytest.mark.asyncio
async def test_import_resolves_nick_only_row_to_existing_jid_ban(temp_db_path, tmp_path):
    csv_file = tmp_path / "nick-only.csv"
    csv_file.write_text(
        "jid,nick,until,issuer,comment\n"
        ",Nick,0,imported,resolved by nick\n",
        encoding="utf-8",
    )
    bot = ImportBot()
    await bot.setup_db(create_startup_backup=False)
    try:
        await bot.upsert_ban_db("user@example.org", "Nick", 0, "seed", "existing")

        successful, skipped, errors = await bot.import_bans_from_csv(str(csv_file))

        assert successful == 0
        assert skipped == 1
        assert errors == []

        async with bot.db.execute(
            """
            SELECT target_type, target, jid, nick, issuer, comment
            FROM bans
            ORDER BY target_type, target
            """
        ) as cursor:
            rows = await cursor.fetchall()

        assert rows == [
            (
                "jid",
                "user@example.org",
                "user@example.org",
                "nick",
                "seed",
                "existing",
            )
        ]
    finally:
        await bot.db.close()


@pytest.mark.asyncio
async def test_import_staged_duplicates_keep_stronger_row(temp_db_path, tmp_path):
    csv_file = tmp_path / "duplicates.csv"
    csv_file.write_text(
        "jid,nick,until,issuer,comment\n"
        "user@example.org,Nick,1000,imported,weaker temp\n"
        "user@example.org,Nick,0,imported,stronger permanent\n",
        encoding="utf-8",
    )
    bot = ImportBot()
    await bot.setup_db(create_startup_backup=False)
    try:
        successful, skipped, errors = await bot.import_bans_from_csv(str(csv_file))

        assert successful == 1
        assert skipped == 1
        assert errors == []

        async with bot.db.execute(
            "SELECT target_type, target, until, comment FROM bans WHERE target = 'user@example.org'"
        ) as cursor:
            row = await cursor.fetchone()

        assert row == ("jid", "user@example.org", 0, "stronger permanent")
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


@pytest.mark.asyncio
async def test_export_command_lists_and_deletes_latest(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    bot = ImportBot()
    bot._cache_ban("one@example.org", "One", 0, "tester", "one")

    ok, _message = await bot.export_bans_to_csv()
    assert ok is True
    bot._cache_ban("two@example.org", "Two", 0, "tester", "two")
    ok, _message = await bot.export_bans_to_csv()
    assert ok is True

    await bot.cmd_export(["list"], "admin@conference.example.org")
    body = bot.sent[-1]["mbody"]
    assert "Managed Ban Exports" in body
    assert "bans_export_" in body
    assert "Keep:" in body

    exports_before = bot.list_export_files()
    assert len(exports_before) == 2
    latest = exports_before[0].path

    await bot.cmd_export(["delete", "latest"], "admin@conference.example.org")
    assert "Export deleted" in bot.sent[-1]["mbody"]
    assert not latest.exists()
    assert len(bot.list_export_files()) == 1


@pytest.mark.asyncio
async def test_export_list_command_paginates(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    export_dir = tmp_path / "data" / "exports"
    export_dir.mkdir(parents=True)
    for index in range(3):
        path = export_dir / f"bans_export_20260101_00000{index}.csv"
        path.write_text("jid,nick,until,issuer,comment\n", encoding="utf-8")
        path.touch()

    bot = ImportBot()
    bot.list_page_size = 2

    await bot.cmd_export(["list"], "admin@conference.example.org")
    first_page = bot.sent[-1]["mbody"]
    assert "Managed Ban Exports (3) - Page 1/2" in first_page
    assert "Use !export list 2 for the next page." in first_page

    await bot.cmd_export(["list", "last"], "admin@conference.example.org")
    last_page = bot.sent[-1]["mbody"]
    assert "Managed Ban Exports (3) - Page 2/2" in last_page

    await bot.cmd_export(["list", "all"], "admin@conference.example.org")
    all_page = bot.sent[-1]["mbody"]
    assert "Managed Ban Exports (3) - All" in all_page


@pytest.mark.asyncio
async def test_export_command_rejects_unknown_delete_target(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    bot = ImportBot()

    await bot.cmd_export(["delete", "../outside.csv"], "admin@conference.example.org")

    assert "Export not found" in bot.sent[-1]["mbody"]


@pytest.mark.asyncio
async def test_export_bans_to_csv_without_bans_returns_message(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    bot = ImportBot()

    ok, message = await bot.export_bans_to_csv()

    assert ok is False
    assert "No bans to export" in message
    assert bot.list_export_files() == []


@pytest.mark.asyncio
async def test_import_dry_run_stages_without_backup_or_database_write(temp_db_path, tmp_path):
    csv_file = tmp_path / "dryrun.csv"
    csv_file.write_text(
        "jid,nick,until,issuer,comment\n"
        "dryrun@example.org,Dry,0,imported,would import\n",
        encoding="utf-8",
    )
    bot = ImportBot()
    await bot.setup_db(create_startup_backup=False)
    try:
        successful, skipped, errors = await bot.import_bans_from_csv(
            str(csv_file),
            actor="admin@example.org",
            dry_run=True,
        )

        assert (successful, skipped, errors) == (1, 0, [])
        assert bot.last_database_backup_file is None
        assert "dryrun@example.org" not in bot.ban_cache
        async with bot.db.execute("SELECT COUNT(*) FROM bans") as cursor:
            row = await cursor.fetchone()
        assert row[0] == 0
    finally:
        await bot.db.close()


@pytest.mark.asyncio
async def test_import_staged_duplicates_keep_later_temporary_row(temp_db_path, tmp_path):
    csv_file = tmp_path / "temp-duplicates.csv"
    csv_file.write_text(
        "jid,nick,until,issuer,comment\n"
        "user@example.org,Nick,1000,imported,weaker temp\n"
        "user@example.org,Nick,2000,imported,stronger temp\n",
        encoding="utf-8",
    )
    bot = ImportBot()
    await bot.setup_db(create_startup_backup=False)
    try:
        successful, skipped, errors = await bot.import_bans_from_csv(str(csv_file))

        assert (successful, skipped, errors) == (1, 1, [])
        async with bot.db.execute(
            "SELECT until, comment FROM bans WHERE target = 'user@example.org'"
        ) as cursor:
            row = await cursor.fetchone()
        assert row == (2000, "stronger temp")
    finally:
        await bot.db.close()
