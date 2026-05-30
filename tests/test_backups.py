"""Managed database backup and restore tests."""

from __future__ import annotations

import os

import pytest

aiosqlite = pytest.importorskip("aiosqlite")

from banbot.backups import BackupMixin
from banbot.cache import CacheMixin
from banbot.db import DatabaseMixin
from banbot.utils import bare_jid


class BackupBot(BackupMixin, DatabaseMixin, CacheMixin):
    bare_jid = staticmethod(bare_jid)

    def __init__(self):
        self.db = None
        self.protected_rooms = set()
        self.ban_cache = {}
        self.ban_index_by_jid = {}
        self.ban_index_by_nick = {}
        self.ban_index_by_domain = {}
        self.events = []
        self.audit_events = []
        self.sent = []
        self.command_prefix = "!"
        self.last_database_backup_file = None
        self.last_database_restore_file = None

    def log_event(self, level, event, **fields):
        self.events.append((level, event, fields))

    async def audit_event(self, event_type, **kwargs):
        self.audit_events.append((event_type, kwargs))

    async def bot_send_message(self, **kwargs):
        self.sent.append(kwargs)

    async def setup_ignorelist(self):
        self.ignorelist_reloaded = True

    async def load_pending_room_invites(self):
        self.invites_reloaded = True


@pytest.fixture
def backup_config(tmp_path, monkeypatch):
    import banbot.backups as backups_module
    import banbot.db as db_module

    db_path = tmp_path / "banbot.sqlite3"
    backup_dir = tmp_path / "backups"
    config_path = tmp_path / "config.py"
    config_path.write_text('JID = "before@example.org"\nPASSWORD = "secret"\n')

    monkeypatch.setattr(backups_module.config, "DB_FILE", str(db_path), raising=False)
    monkeypatch.setattr(backups_module.config, "__file__", str(config_path), raising=False)
    monkeypatch.setattr(backups_module.config, "DB_BACKUP_DIR", str(backup_dir), raising=False)
    monkeypatch.setattr(backups_module.config, "DB_BACKUP_KEEP", 10, raising=False)
    monkeypatch.setattr(backups_module.config, "DB_BACKUP_ON_START", True, raising=False)
    monkeypatch.setattr(db_module, "DB_FILE", str(db_path), raising=False)
    return db_path, backup_dir


@pytest.mark.asyncio
async def test_manual_backup_creates_managed_snapshot(backup_config):
    db_path, backup_dir = backup_config
    bot = BackupBot()
    await bot.setup_db(create_startup_backup=False)
    try:
        ok, message = await bot.create_database_backup("manual", actor="admin@example.org")

        assert ok is True
        assert backup_dir.exists()
        backups = bot.list_database_backups()
        assert len(backups) == 1
        assert backups[0].path.name.startswith(db_path.name + ".snapshot-manual-")
        assert message == str(backups[0].path)
        assert bot.last_database_backup_file == message
        assert (backups[0].path.with_name(backups[0].path.name + ".config.py")).exists()
        assert bot.audit_events[-1][0] == "db_backup_created"
        assert bot.audit_events[-1][1]["actor"] == "admin@example.org"
        assert bot.audit_events[-1][1]["details"]["config_backup"].endswith(".config.py")
        assert bot.events[-1][2]["actor"] == "admin@example.org"
    finally:
        await bot.db.close()


@pytest.mark.asyncio
async def test_backup_list_command_shows_restore_hint(backup_config):
    bot = BackupBot()
    await bot.setup_db(create_startup_backup=False)
    try:
        await bot.create_database_backup("manual")
        await bot.cmd_backup(["list"], "admin@conference.example.org")

        body = bot.sent[-1]["mbody"]
        assert "💾 Database Backups" in body
        assert "snapshot-manual" in body
        assert "config.py" in body
        assert "!restore <filename|latest> confirm" in body
    finally:
        await bot.db.close()


@pytest.mark.asyncio
async def test_startup_snapshot_honors_keep_limit(backup_config, monkeypatch):
    _db_path, backup_dir = backup_config
    import banbot.backups as backups_module

    monkeypatch.setattr(backups_module.config, "DB_BACKUP_KEEP", 2, raising=False)
    bot = BackupBot()
    await bot.setup_db(create_startup_backup=False)
    await bot.db.close()

    # Force unique names while keeping deterministic ordering.
    for suffix in range(4):
        bot = BackupBot()
        await bot.setup_db(create_startup_backup=True)
        await bot.db.close()
        startup_backups = [path for path in backup_dir.glob("*.snapshot-startup-*") if not path.name.endswith(".config.py")]
        os.utime(sorted(startup_backups)[-1], (1000 + suffix, 1000 + suffix))

    backups = BackupBot().list_database_backups()
    assert len(backups) == 2
    assert all("snapshot-startup" in item.name for item in backups)


@pytest.mark.asyncio
async def test_restore_requires_confirmation_and_restores_database(backup_config):
    db_path, _backup_dir = backup_config
    bot = BackupBot()
    await bot.setup_db(create_startup_backup=False)
    try:
        await bot.db.execute("INSERT INTO rooms(room) VALUES ('before@conference.example.org')")
        await bot.db.commit()
        ok, backup_path = await bot.create_database_backup("manual")
        assert ok is True

        import banbot.backups as backups_module
        config_path = os.fspath(backups_module.config.__file__)
        with open(config_path, "w", encoding="utf-8") as handle:
            handle.write('JID = "after@example.org"\nPASSWORD = "changed"\n')

        await bot.db.execute("DELETE FROM rooms")
        await bot.db.execute("INSERT INTO rooms(room) VALUES ('after@conference.example.org')")
        await bot.db.commit()

        await bot.cmd_restore(["latest"], "admin@conference.example.org")
        assert "Confirm with:" in bot.sent[-1]["mbody"]

        ok, message = await bot.restore_database_backup("latest", actor="admin@example.org")
        assert ok is True
        assert "Database restored" in message
        assert "config.py was restored" in message
        assert bot.last_database_restore_file == backup_path
        assert "before@conference.example.org" in bot.protected_rooms
        assert "after@conference.example.org" not in bot.protected_rooms
        assert db_path.exists()
        with open(config_path, encoding="utf-8") as handle:
            assert 'JID = "before@example.org"' in handle.read()
        assert bot.audit_events[-1][0] == "db_backup_restored"
        assert bot.audit_events[-1][1]["details"]["config_restored"] is True
    finally:
        if bot.db:
            await bot.db.close()


@pytest.mark.asyncio
async def test_backup_command_and_restore_alias(backup_config):
    bot = BackupBot()
    await bot.setup_db(create_startup_backup=False)
    try:
        await bot.cmd_backup([], "admin@conference.example.org", actor="admin@example.org")
        assert "Database/config backup created" in bot.sent[-1]["mbody"]
        assert bot.audit_events[-1][0] == "db_backup_created"
        assert bot.audit_events[-1][1]["actor"] == "admin@example.org"

        await bot.cmd_backup(["restore", "latest"], "admin@conference.example.org")
        assert "Confirm with:" in bot.sent[-1]["mbody"]
    finally:
        await bot.db.close()


@pytest.mark.asyncio
async def test_restore_safety_backup_records_actor(backup_config):
    bot = BackupBot()
    await bot.setup_db(create_startup_backup=False)
    try:
        ok, _backup_path = await bot.create_database_backup("manual", actor="creator@example.org")
        assert ok is True

        ok, message = await bot.restore_database_backup("latest", actor="restorer@example.org")
        assert ok is True
        assert "Database restored" in message

        created_events = [event for event in bot.audit_events if event[0] == "db_backup_created"]
        restored_events = [event for event in bot.audit_events if event[0] == "db_backup_restored"]

        assert created_events[-1][1]["actor"] == "restorer@example.org"
        assert created_events[-1][1]["details"]["reason"] == "before-restore"
        assert restored_events[-1][1]["actor"] == "restorer@example.org"
    finally:
        if bot.db:
            await bot.db.close()
