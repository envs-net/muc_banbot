#!/usr/bin/env python3

# muc_banbot: XMPP Multi-Room Ban Management Bot
# Author: creme <xmpp:creme@envs.net>
# License: MIT

__version__ = "1.4.0"

import os
import csv
import config
import asyncio
import logging
import time
import aiosqlite
import pathlib
import importlib
import hashlib
import re
import psutil
import urllib.request
from datetime import datetime
from slixmpp import ClientXMPP
from slixmpp.exceptions import IqError, IqTimeout
from slixmpp.xmlstream import ET
from slixmpp.stanza.presence import Presence

from config import JID, RESSOURCE, PASSWORD, ADMIN_ROOM, NICK, DB_FILE

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)


# ---------- HELPERS ----------
def parse_duration(s: str) -> int:
    """
    Parse a duration string into seconds.
    Supported suffixes: s=seconds, m=minutes, h=hours, d=days
    Example: '10m' -> 600
    """
    units = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    if len(s) < 2 or s[-1].lower() not in units:
        raise ValueError("Invalid duration format (use 10s, 10m, 2h, 1d)")
    try:
        value = int(s[:-1])
    except ValueError:
        raise ValueError("Invalid duration number")
    return value * units[s[-1].lower()]

def human_time(seconds: int) -> str:
    """
    Convert seconds to human-readable string.
    Example: 3661 -> '1h 1m 1s'
    """
    seconds = int(seconds)
    if seconds <= 0:
        return "permanent"
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    d, h = divmod(h, 24)
    parts = []
    if d: parts.append(f"{d}d")
    if h: parts.append(f"{h}h")
    if m: parts.append(f"{m}m")
    if s: parts.append(f"{s}s")
    return " ".join(parts)

def validate_jid_format(jid: str) -> bool:
    """Validate JID format (user@domain.tld)."""
    if not jid or "@" not in jid:
        return False
    parts = jid.split("@")
    if len(parts) != 2:
        return False
    local, domain = parts
    # Basic validation: non-empty parts with valid characters
    if not local or not domain or "." not in domain:
        return False
    return True

def validate_domain_ban(domain: str) -> tuple[bool, str]:
    """
    Validate domain ban format.
    - Blocks: *.tld (generic)
    - Allows: *.domain.tld (specific)
    Returns: (is_valid: bool, error_message: str)
    """
    parts = domain.split(".")
    # Need at least 2 parts: *.domain.tld → ["", "domain", "tld"]
    if len(parts) < 3:
        return False, f"❌ Domain '{domain}' is too generic. Specify more precise domain (e.g., *.domain.tld)."
    return True, ""


# ---------- BAN BOT ----------
class BanBot(ClientXMPP):
    def __init__(self, jid: str, password: str, resource: str = None):
        """
        Initialize the bot with:
        - Connection info (jid/resource/password)
        - RAM caches for bans and admin state
        - Semaphores to avoid flooding XMPP server
        - Uptime tracking for bot and server connection
        Sets up DB, protected rooms, occupants dicts, and registers XMPP plugins.
        """
        # Combine JID with resource if provided
        if resource:
            full_jid = f"{jid}/{resource}"
        else:
            full_jid = jid

        super().__init__(full_jid, password)
        self.db: aiosqlite.Connection | None = None

        # --- Concurrency limit for MUC write operations ---
        # Prevents flooding the XMPP server with too many IQ stanzas at once
        self.muc_write_limit = getattr(config, "MUC_WRITE_SEMAPHORE", 5)
        self.muc_write_semaphore = asyncio.Semaphore(self.muc_write_limit)

        # Ban Cache: key -> (jid_bare, nick, until, issuer, comment)
        self.ban_cache: dict[str, tuple[str | None, str | None, int, str | None, str | None]] = {}

        # Ban indexes
        self.ban_index_by_jid: dict[str, tuple] = {}
        self.ban_index_by_nick: dict[str, tuple] = {}
        self.ban_index_by_domain: dict[str, list] = {}

        self.bot_admin_state: dict[str, bool] = {}
        self.occupants: dict[str, dict] = {}
        self.protected_rooms: set[str] = set()
        self.registered_rooms: set[str] = set()
        self.room_join_time: dict[str, float] = {}
        self.reconnecting = False
        self.health_check_task: asyncio.Task | None = None
        self.unban_task: asyncio.Task | None = None

        # --- Uptime tracking ---
        self.bot_start_time = time.time()
        self.server_connect_time = None

        # --- default config options ---
        self.command_prefix: str = "!"
        self.announce_startup: bool = True
        self.announce_sync_details: bool = True
        self.show_ban_in_muc: bool = False
        self.allow_user_cmds: bool = True
        self.health_check_interval: int = 300
        self.unban_check_interval: int = 60
        self.max_tempban_days: int = 30

        # --- update check ---
        self.version_check_task: asyncio.Task | None = None
        self.version_check_enabled: bool = False
        self.version_check_interval: int = 3600
        self.version_check_url: str | None = None
        self.last_version_check_result: str | None = None
        self.last_update_notified_version: str | None = None

        # --- apply config ---
        self.apply_runtime_config()

        # --- Register XMPP plugins ---
        self.register_plugin("xep_0030")  # Service Discovery
        self.register_plugin("xep_0045")  # Multi-User Chat
        self.register_plugin('xep_0054')  # vCard
        self.register_plugin('xep_0084')  # Modern Avatar
        self.register_plugin('xep_0153')  # vCard Avatar compatibility

        # --- Event handlers ---
        self.add_event_handler("session_start", self.start)
        self.add_event_handler("message", self.on_direct_message)
        self.add_event_handler("groupchat_message", self.on_message)
        self.add_event_handler("groupchat_presence", self.on_muc_presence)
        self.add_event_handler("disconnected", self.on_disconnect)
        self.add_event_handler("connection_failed", self.on_disconnect)


    # ---------- RUNTIME CONFIG ----------
    def apply_runtime_config(self) -> None:
        """Load reloadable runtime settings from config."""
        new_semaphore_value = getattr(config, "MUC_WRITE_SEMAPHORE", 5)
        if new_semaphore_value != self.muc_write_limit:
            old_value = self.muc_write_limit
            self.muc_write_limit = new_semaphore_value
            self.muc_write_semaphore = asyncio.Semaphore(new_semaphore_value)
            log.info("🔄 MUC_WRITE_SEMAPHORE updated: %d → %d", old_value, new_semaphore_value)

        self.command_prefix = str(getattr(config, "COMMAND_PREFIX", "!")).strip() or "!"
        self.announce_startup = getattr(config, "ANNOUNCE_STARTUP", True)
        self.announce_sync_details = getattr(config, "ANNOUNCE_SYNC_DETAILS", True)
        self.show_ban_in_muc = getattr(config, "SHOW_BAN_IN_MUC", False)
        self.allow_user_cmds = getattr(config, "ALLOW_USER_COMMANDS_IN_PROTECTED_ROOMS", True)
        self.health_check_interval = max(60, getattr(config, "HEALTH_CHECK_INTERVAL", 300))
        self.unban_check_interval = getattr(config, "UNBAN_CHECK_INTERVAL", 60)
        self.max_tempban_days = max(1, min(365, getattr(config, "MAX_TEMPBAN_DAYS", 30)))

        self.version_check_enabled = getattr(config, "VERSION_CHECK_ENABLED", False)
        self.version_check_interval = max(300, getattr(config, "VERSION_CHECK_INTERVAL", 3600))
        self.version_check_url = str(getattr(config, "VERSION_CHECK_URL", "")).strip() or None

    async def reload_runtime_config(self) -> None:
        """Reload config.py and re-apply runtime settings."""
        importlib.reload(config)
        self.apply_runtime_config()
        await self.update_vcard()


    # ---------- TASKS ----------
    async def stop_background_tasks(self) -> None:
        """Cancel running background tasks before starting new ones."""
        for task in (self.unban_task, self.health_check_task, self.version_check_task):
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass


    # ---------- SESSION START ----------
    async def start(self, _) -> None:
        """
        Called when the XMPP session starts.
        - Initializes DB
        - Joins admin and protected rooms
        - Waits for occupants
        - Syncs admins
        - Applies all bans in parallel
        - Starts unban worker
        """
        await self.stop_background_tasks()

        await self.setup_db()
        await self.load_bans_from_db()

        if not self.reconnecting:
            # First connection only
            self.bot_start_time = time.time()
        else:
            log.info("🔄 Reconnected successfully")

        self.send_presence()
        await self.get_roster()

        await asyncio.sleep(3)

        # --- Record server connection time ---
        self.server_connect_time = time.time()

        # --- Join admin room ---
        self.plugin["xep_0045"].join_muc(ADMIN_ROOM, NICK)
        self.room_join_time[ADMIN_ROOM] = time.time()

        if ADMIN_ROOM not in self.registered_rooms:
            self.add_event_handler(f"muc::{ADMIN_ROOM}::got_online", self.muc_online)
            self.add_event_handler(f"muc::{ADMIN_ROOM}::got_offline", self.muc_offline)
            self.registered_rooms.add(ADMIN_ROOM)

        # --- Join protected rooms ---
        for room in self.protected_rooms:
            self.plugin["xep_0045"].join_muc(room, NICK)
            self.room_join_time[room] = time.time()
            if room not in self.registered_rooms:
                self.add_event_handler(f"muc::{room}::got_online", self.muc_online)
                self.add_event_handler(f"muc::{room}::got_offline", self.muc_offline)
                self.registered_rooms.add(room)

        # --- Wait for occupants to populate ---
        await self.wait_for_occupants(timeout=20)

        # --- Check admin rights in all protected rooms ---
        await self.check_bot_admin_rights()

        # --- Sync Admins ---
        await self.sync_admins(announce=False)

        # --- Apply all bans in parallel at startup ---
        await self.sync_bans_startup()

        # --- Start unban worker ---
        self.unban_task = asyncio.create_task(self.unban_worker())

        # --- Start health check worker ---
        self.health_check_task = asyncio.create_task(self.health_check_worker())

        # --- Start version check worker ---
        if self.version_check_enabled and self.version_check_url:
            self.version_check_task = asyncio.create_task(self.version_check_worker())

        # --- Set Bot vCard ---
        await self.update_vcard()

        self.reconnecting = False

        # Send startup notification if enabled
        if self.announce_startup:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self.send_message(
                mto=ADMIN_ROOM,
                mbody=f"✅ Bot has restarted and synced all bans. ({timestamp})",
                mtype="groupchat"
            )

        log.info("✅ Bot started, all rooms joined and bans applied")


    async def on_disconnect(self, _) -> None:
        log.warning("⚠️ Disconnected from server")

        self.reconnecting = True

        # runtime state reset
        self.occupants.clear()
        self.bot_admin_state.clear()
        self.room_join_time.clear()
        log.info("🧹 Cleaned up occupants dictionary and states")

        delay = 5

        while not self.connected:
            try:
                log.info("🔄 Attempting reconnect in %ds...", delay)
                await asyncio.sleep(delay)

                if self.connect():
                    log.info("🔌 Reconnect initiated")
                    return

            except Exception as e:
                log.error("Reconnect error: %s", e)

            delay = min(delay * 2, 300)  # exponential backoff max 5min


    # ---------- HEALTH CHECK ----------
    async def health_check_worker(self) -> None:
        """
        Periodically check connection status of all protected rooms.
        Verifies bot is still in rooms and has admin rights.
        Uses self.health_check_interval (reloadable via !reloadconfig).
        """
        while True:
            try:
                await asyncio.sleep(self.health_check_interval)

                for room in self.protected_rooms:
                    try:
                        # Check if bot is still in room
                        occ = self.occupants.get(room, {})
                        bot_in_room = any(nick.lower() == NICK.lower() for nick in occ.keys())
                        if not bot_in_room:
                            log.warning("⚠️ Health check: Bot not found in occupants for room %s", room)
                            self.send_message(
                                mto=ADMIN_ROOM,
                                mbody=f"⚠️ Health check warning: Bot not in room {room} occupants",
                                mtype="groupchat"
                            )
                            continue

                        # Check admin rights
                        if not self.is_bot_admin_or_owner(room):
                            log.warning("⚠️ Health check: Bot lost admin rights in %s", room)
                            self.send_message(
                                mto=ADMIN_ROOM,
                                mbody=f"⚠️ Health check: Bot lost admin/owner rights in {room}",
                                mtype="groupchat"
                            )

                    except Exception as e:
                        log.warning("Health check error for %s: %s", room, e)

            except asyncio.CancelledError:
                log.info("health_check_worker cancelled")
                raise

            except Exception as e:
                log.warning("Error in health_check_worker: %s", e)
                await asyncio.sleep(10)


    # ---------- VERSION CHECK ----------

    def _parse_version_tuple(self, version: str) -> tuple[int, ...]:
        parts = re.findall(r"\d+", version)
        return tuple(int(p) for p in parts)

    def _is_remote_version_newer(self, remote_version: str, local_version: str) -> bool:
        return self._parse_version_tuple(remote_version) > self._parse_version_tuple(local_version)

    def _fetch_latest_release_version_sync(self) -> str:
        """
        Fetch the latest GitHub release version by following the /releases/latest redirect.
        Example final URL:
            https://github.com/envs-net/muc_banbot/releases/tag/v1.3.0
        Returns:
            1.3.0
        """
        if not self.version_check_url:
            raise ValueError("VERSION_CHECK_URL is not configured")

        req = urllib.request.Request(
            self.version_check_url,
            headers={"User-Agent": f"muc_banbot/{__version__}"}
        )

        with urllib.request.urlopen(req, timeout=15) as response:
            final_url = response.geturl()

        marker = "/releases/tag/"
        if marker not in final_url:
            raise ValueError(f"Unexpected release redirect URL: {final_url}")

        tag = final_url.split(marker, 1)[1].strip().strip("/")
        if not tag:
            raise ValueError("Could not extract release tag from redirect URL")

        return tag.lstrip("v")

    async def check_for_updates_once(
        self,
        announce: bool = True
    ) -> tuple[bool, str | None, str | None]:
        """
        Check once whether a newer bot version is available.
        Returns: (is_update_available, remote_version, error_message)
        """
        if not self.version_check_enabled or not self.version_check_url:
            return False, None, "Version check is disabled or URL is missing"

        try:
            remote_version = await asyncio.to_thread(self._fetch_latest_release_version_sync)
            self.last_version_check_result = remote_version

            current_version = __version__.lstrip("v").strip()

            if self._is_remote_version_newer(remote_version, current_version):
                log.info(
                    "⬆️ New bot version available: remote=%s local=%s url=%s",
                    remote_version,
                    current_version,
                    self.version_check_url
                )

                if announce and self.last_update_notified_version != remote_version:
                    self.send_message(
                        mto=ADMIN_ROOM,
                        mbody=(
                            f"⬆️ New bot version available: {remote_version}\n"
                            f"Current version: {current_version}\n"
                            f"Release page: {self.version_check_url}"
                        ),
                        mtype="groupchat"
                    )
                    self.last_update_notified_version = remote_version

                return True, remote_version, None

            return False, remote_version, None

        except Exception as e:
            log.warning("Version check failed: %s", e)
            return False, None, str(e)

    async def version_check_worker(self) -> None:
        """
        Periodically check whether a newer bot version is available.
        """
        while True:
            try:
                await self.check_for_updates_once(announce=True)

            except asyncio.CancelledError:
                log.info("version_check_worker cancelled")
                raise

            except Exception as e:
                log.warning("Error in version_check_worker: %s", e)

            await asyncio.sleep(self.version_check_interval)


    # ---------- BASIC HELPERS ----------
    @staticmethod
    def bare_jid(jid: str | None) -> str | None:
        """
        Return the bare JID (without resource)
        e.g., user@server/resource -> user@server
        """
        return jid.split("/")[0].lower() if jid else None

    @staticmethod
    def safe_jid(text) -> str:
        return str(text).replace("@", "@\u200b")

    def send_ephemeral(self, mto: str, mbody: str) -> None:
        """Send a message to a room without storing it."""
        msg = self.Message()
        msg["to"] = mto
        msg["type"] = "groupchat"
        msg["body"] = mbody
        no_store = ET.Element("{urn:xmpp:hints}no-store")
        msg.append(no_store)
        msg.send()

    def notify_protected(self, room: str, message: str) -> None:
        """Notify users in protected rooms if SHOW_BAN_IN_MUC=True"""
        if self.show_ban_in_muc:
            self.send_ephemeral(room, message)

    def user_cmds_allowed(self, room: str) -> bool:
        """Check if user commands are allowed in this room."""
        return (
            room == ADMIN_ROOM or
            (room in self.protected_rooms and self.allow_user_cmds)
        )

    # ---------- AUTH / PERMISSIONS ----------
    def is_admin_or_owner(self, room: str, nick: str | None = None, jid: str | None = None) -> bool:
        """Check if a user is admin or owner in a room."""
        occ = self.occupants.get(room, {})
        for n, info in occ.items():
            if nick and n.lower() == nick.lower():
                return info.get("affiliation") in ("owner", "admin")
            if jid and info.get("jid") and self.bare_jid(info["jid"]) == self.bare_jid(jid):
                return info.get("affiliation") in ("owner", "admin")
        return False

    def is_bot_admin_or_owner(self, room: str) -> bool:
        """
        Check if the bot itself is admin or owner in a given room.
        """
        occ = self.occupants.get(room, {})
        bot_info = occ.get(NICK)

        if not bot_info:
            log.warning("⚠️ Bot nick not found in occupants for room %s", room)
            return False

        return bot_info.get("affiliation") in ("owner", "admin")

    # ---------- SENDER AUTH ----------
    def is_authorized(self, msg) -> bool:
        """
        Check if a message sender is authorized to issue admin commands.
        """
        if msg["from"].bare != ADMIN_ROOM:
            return False
        info = self.occupants.get(ADMIN_ROOM, {}).get(msg["mucnick"])
        return info and info.get("affiliation") in ("owner", "admin")


    # ---------- WAIT FOR OCCUPANTS ----------
    async def wait_for_occupants(self, timeout: int = 20) -> None:
        """
        Wait until all protected rooms and admin room have at least one occupant loaded.
        Fallback to timeout if rooms are empty. Helps avoid race conditions at startup.
        """
        start = time.time()
        while time.time() - start < timeout:
            ready = True
            for r in self.protected_rooms | {ADMIN_ROOM}:
                occ = self.occupants.get(r)
                if occ is None or len(occ) == 0:
                    ready = False
                    break
            if ready:
                return
            await asyncio.sleep(2)
        log.warning("Timeout waiting for occupants; some users may not be kicked immediately")

    async def wait_for_bot_online(self, room: str, timeout: int = 10) -> bool:
        """
        Wait until the bot is recognized as a participant in a room.
        Prevents race conditions after joining.
        """
        for _ in range(timeout):
            occ = self.occupants.get(room, {})
            if NICK in occ:
                return True
            await asyncio.sleep(1)
        log.warning("Bot not recognized in %s after %ds", room, timeout)
        return False


    async def verify_admin_rights(self, room: str) -> bool:
        """
        Server-side check if the bot is actually admin/owner.
        Returns True if yes, False otherwise.
        """
        try:
            owners = await self.plugin["xep_0045"].get_users_by_affiliation(room, "owner")
            admins = await self.plugin["xep_0045"].get_users_by_affiliation(room, "admin")
            #bare_bot_jid = self.bare_jid(NICK)  # optional: use room-specific mapping if needed
            bare_bot_jid = self.boundjid.bare

            for jid in owners + admins:
                if str(jid).split("/")[0].lower() == bare_bot_jid.lower():
                    return True
            return False
        except (IqError, IqTimeout) as e:
            log.warning("Server check failed for %s: %s", room, e)
            # Return False on error → Alarm or Retry
            return False

    async def check_bot_admin_rights(self) -> None:
        """
        Check all protected rooms after startup and report
        where the bot lacks admin/owner rights.
        """

        missing = []

        for room in self.protected_rooms:

            # Wait until bot appears in occupants
            for _ in range(10):  # max 10 seconds per room
                occ = self.occupants.get(room, {})
                if NICK in occ:
                    break
                await asyncio.sleep(1)

            # --- Initialize admin state after join (ONLY ONCE) ---
            try:
                self.bot_admin_state[room] = self.is_bot_admin_or_owner(room)
                if not self.bot_admin_state[room]:
                    missing.append(room)
            except Exception as e:
                log.warning("Error checking admin rights in %s: %s", room, e)
                missing.append(room)

        if missing:
            msg = (
                "⚠️ Bot is missing admin/owner rights in the following rooms:\n"
                + "\n".join(missing)
            )
            self.send_message(
                mto=ADMIN_ROOM,
                mbody=msg,
                mtype="groupchat"
            )
            log.warning("Bot missing admin rights in rooms: %s", missing)
        else:
            msg = "✅ Bot has admin/owner rights in all protected rooms."
            self.send_message(
                mto=ADMIN_ROOM,
                mbody=msg,
                mtype="groupchat"
            )
            log.info("Bot has admin rights in all protected rooms.")


    # ---------- DATABASE SETUP ----------
    async def setup_db(self) -> None:
        """Initialize SQLite DB, create tables if missing, migrate columns, create indexes."""
        self.db = await aiosqlite.connect(DB_FILE)

        # --- Create bans table if missing ---
        async with self.db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='bans'"
        ) as cursor:
            row = await cursor.fetchone()

        if not row:
            await self.db.execute("""
            CREATE TABLE bans (
                jid TEXT PRIMARY KEY,
                nick TEXT,
                until INTEGER,
                issuer TEXT,
                comment TEXT
            )""")
        else:
            async with self.db.execute("PRAGMA table_info(bans)") as cursor:
                columns = [r[1] async for r in cursor]
            if "nick" not in columns:
                await self.db.execute("ALTER TABLE bans ADD COLUMN nick TEXT")
                log.info("DB migration: 'nick' column added.")

        # --- Rooms table ---
        await self.db.execute("""
        CREATE TABLE IF NOT EXISTS rooms (
            room TEXT PRIMARY KEY
        )""")
        await self.db.commit()

        # --- Create indexes for performance ---
        await self.db.execute("CREATE INDEX IF NOT EXISTS idx_bans_jid ON bans(jid)")
        await self.db.execute("CREATE INDEX IF NOT EXISTS idx_bans_nick ON bans(LOWER(nick))")
        await self.db.execute("CREATE INDEX IF NOT EXISTS idx_bans_until ON bans(until)")
        await self.db.commit()
        log.info("✅ Database indexes created/verified")

        # --- Load protected rooms ---
        async with self.db.execute("SELECT room FROM rooms") as cursor:
            rows = await cursor.fetchall()
            for (room,) in rows:
                self.protected_rooms.add(room)


    # ---------- EXPORT / IMPORT ----------
    async def export_bans_to_csv(self) -> tuple[bool, str]:
        """
        Export all bans to a CSV file.
        Returns: (success: bool, message: str)
        """
        try:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"bans_export_{timestamp}.csv"

            # Use cache if available (instant!) otherwise query DB
            if self.ban_cache:
                rows = [(v[0], v[1], v[2], v[3], v[4]) for v in self.ban_cache.values()]
                log.info("📤 Export using cache (%d bans)", len(rows))
            else:
                async with self.db.execute(
                    "SELECT jid, nick, until, issuer, comment FROM bans"
                ) as cursor:
                    rows = await cursor.fetchall()
                log.info("📤 Export using database query (%d bans)", len(rows))

            if not rows:
                return False, "❌ No bans to export."

            try:
                with open(filename, "w", newline="", encoding="utf-8") as csvfile:
                    writer = csv.writer(csvfile)
                    writer.writerow(["jid", "nick", "until", "issuer", "comment"])
                    for jid, nick, until, issuer, comment in rows:
                        writer.writerow([
                            jid or "",
                            nick or "",
                            until if until is not None else "",
                            issuer or "",
                            comment or ""
                        ])
                log.info("✅ Exported %d bans to %s", len(rows), filename)
                return True, f"✅ Exported {len(rows)} bans to {filename}"
            except IOError as e:
                log.error("File I/O error during export: %s", e)
                return False, f"❌ Failed to write file: {e}"

        except Exception as e:
            log.error("Export error: %s", e)
            return False, f"❌ Export failed: {e}"

    async def import_bans_from_csv(self, filename: str) -> tuple[int, int, list[str]]:
        """
        Import bans from a CSV file.
        Returns: (successful_count, skipped_count, error_messages)
        """
        successful = 0
        skipped = 0
        errors = []
        bans_to_insert = []  # Collect all bans for batch insert

        try:
            if not pathlib.Path(filename).exists():
                errors.append(f"❌ File not found: {filename}")
                return 0, 0, errors

            try:
                with open(filename, "r", encoding="utf-8") as csvfile:
                    reader = csv.DictReader(csvfile)

                    if not reader.fieldnames or set(reader.fieldnames) != {"jid", "nick", "until", "issuer", "comment"}:
                        errors.append("❌ Invalid CSV header. Expected: jid,nick,until,issuer,comment")
                        return 0, 0, errors

                    rows = list(reader)

            except IOError as e:
                errors.append(f"❌ File I/O error: {e}")
                log.error("Import file error: %s", e)
                return 0, 0, errors

            for row_num, row in enumerate(rows, start=2):
                try:
                    jid = (row.get("jid") or "").strip() or None
                    nick = (row.get("nick") or "").strip() or None
                    until_str = (row.get("until") or "").strip()
                    issuer = (row.get("issuer") or "").strip() or "import"
                    comment = (row.get("comment") or "").strip() or None

                    # Require at least one of jid or nick
                    if not jid and not nick:
                        errors.append(f"Row {row_num}: At least one of jid or nick required")
                        skipped += 1
                        continue

                    # Validate JID format if present
                    if jid and not validate_jid_format(jid):
                        errors.append(f"Row {row_num}: Invalid JID format: {jid}")
                        skipped += 1
                        continue

                    # Parse until timestamp
                    try:
                        until = int(until_str) if until_str else 0
                        if until < 0:
                            errors.append(f"Row {row_num}: until must be >= 0 (got {until})")
                            skipped += 1
                            continue
                    except ValueError:
                        errors.append(f"Row {row_num}: until must be a valid number or empty (got '{until_str}')")
                        skipped += 1
                        continue

                    normalized_jid = jid.lower() if jid else None
                    normalized_nick = nick.lower() if nick else None
                    lookup_key = normalized_jid or normalized_nick

                    # Check for existing permanent ban — skip if both are permanent
                    if lookup_key in self.ban_cache:
                        existing = self.ban_cache[lookup_key]
                        existing_until = existing[2]
                        if existing_until <= 0 and until <= 0:
                            log.info("Row %d: Ban already exists for %s (permanent), skipping", row_num, lookup_key)
                            skipped += 1
                            continue

                    # Collect for batch insert
                    bans_to_insert.append((
                        normalized_jid,
                        normalized_nick,
                        until,
                        issuer,
                        comment
                    ))

                    # Update cache AND indexes immediately (for deduplication check in next rows)
                    self._cache_ban(normalized_jid, normalized_nick, until, issuer, comment)
                    successful += 1

                except Exception as e:
                    errors.append(f"Row {row_num}: {e}")
                    skipped += 1

            # Batch insert all bans at once
            if bans_to_insert:
                try:
                    await self.db.executemany(
                        "REPLACE INTO bans (jid, nick, until, issuer, comment) VALUES (?, ?, ?, ?, ?)",
                        bans_to_insert
                    )
                    await self.db.commit()
                    log.info("✅ Batch inserted %d bans", len(bans_to_insert))
                except Exception as e:
                    log.error("Batch insert failed: %s", e)
                    await self.load_bans_from_db()
                    errors.append(f"❌ Database batch insert failed: {e}")
                    return 0, 0, errors

                # Indexes already updated during import loop - no need to rebuild

            log.info("Import complete: %d successful, %d skipped", successful, skipped)

        except Exception as e:
            errors.append(f"❌ Import failed: {e}")
            log.error("Import error: %s", e)

        return successful, skipped, errors


    # ---------- CACHE HELPERS ----------
    def _build_ban_tuple(
        self,
        jid: str | None,
        nick: str | None,
        until: int,
        issuer: str | None,
        comment: str | None,
    ) -> tuple[str | None, str | None, int, str | None, str | None]:
        """Return a normalized ban tuple for caches and indexes."""
        return (
            self.bare_jid(jid) if jid and not jid.startswith("*.") else (jid.lower() if jid else None),
            nick.lower() if nick else None,
            until,
            issuer,
            comment,
        )

    def _cache_ban(
        self,
        jid: str | None,
        nick: str | None,
        until: int,
        issuer: str | None,
        comment: str | None,
    ) -> None:
        """Store a single ban consistently in cache and indexes."""
        ban_tuple = self._build_ban_tuple(jid, nick, until, issuer, comment)
        normalized_jid, normalized_nick, *_ = ban_tuple
        cache_key = normalized_jid or normalized_nick
        if not cache_key:
            return

        self.ban_cache[cache_key] = ban_tuple

        if normalized_jid and not normalized_jid.startswith("*."):
            self.ban_index_by_jid[normalized_jid] = ban_tuple

        if normalized_nick:
            self.ban_index_by_nick[normalized_nick] = ban_tuple

        if normalized_jid and normalized_jid.startswith("*."):
            domain = normalized_jid[2:]
            self.ban_index_by_domain[domain] = [ban_tuple]

    def _remove_ban_from_cache(self, identifier: str, ban_jid: str | None = None, ban_nick: str | None = None) -> None:
        """Remove a single JID/nick ban consistently from cache and indexes."""
        normalized_identifier = identifier.lower() if identifier else identifier
        normalized_jid = self.bare_jid(ban_jid) if ban_jid and not ban_jid.startswith("*.") else (ban_jid.lower() if ban_jid else None)
        normalized_nick = ban_nick.lower() if ban_nick else None

        self.ban_cache.pop(normalized_identifier, None)
        if normalized_jid:
            self.ban_cache.pop(normalized_jid, None)
        if normalized_nick:
            self.ban_cache.pop(normalized_nick, None)

        if normalized_jid and not normalized_jid.startswith("*."):
            self.ban_index_by_jid.pop(normalized_jid, None)
        if normalized_nick:
            self.ban_index_by_nick.pop(normalized_nick, None)

    def _remove_domain_bans_from_cache(self, domain: str) -> None:
        """Remove all wildcard domain bans associated with a domain from cache and indexes."""
        domain = domain.lower()
        wildcard_jid = f"*.{domain}"

        self.ban_cache.pop(wildcard_jid, None)
        self.ban_index_by_domain.pop(domain, None)

    # ---------- BAN CACHE ----------
    async def load_bans_from_db(self) -> None:
        """
        Load all bans from the database into RAM cache with O(1) lookup indexes.
        """
        async with self.db.execute(
            "SELECT jid, nick, until, issuer, comment FROM bans"
        ) as cursor:
            rows = await cursor.fetchall()

        # Reset all caches and indexes
        self.ban_cache.clear()
        self.ban_index_by_jid.clear()
        self.ban_index_by_nick.clear()
        self.ban_index_by_domain.clear()

        for jid, nick, until, issuer, comment in rows:
            self._cache_ban(jid, nick, until, issuer, comment)

        log.info("✅ Loaded %d bans", len(self.ban_cache))


    # ---------- VCARD ----------
    async def update_vcard(self) -> bool:
        """
        Update bot vCard and avatar information.
        - Avatar: optional (from config.AVATAR_PATH)
        - vCard fields: always updated (VCARD_NICKNAME, VCARD_FN, etc.)
        Updates:
          - XEP-0054 (vCard photo + optional vCard fields)
          - XEP-0084 (User Avatar / vCard4) - only if avatar present
          - XEP-0153 (Avatar hash in presence) - only if avatar present
        """
        avatar_path = getattr(config, "AVATAR_PATH", None)
        image_data = None
        avatar_type = None

        # --- Try to load avatar if path is provided ---
        if avatar_path and pathlib.Path(avatar_path).exists():
            try:
                with open(avatar_path, "rb") as f:
                    image_data = f.read()
                avatar_type = f"image/{pathlib.Path(avatar_path).suffix.lstrip('.').lower()}"
                log.info("✅ Avatar loaded from: %s", avatar_path)
            except (FileNotFoundError, IOError) as e:
                log.warning("⚠️ Failed to load avatar image: %s", e)
        else:
            if avatar_path:
                log.warning("⚠️ AVATAR_PATH not set or file does not exist: %s", avatar_path)

        # --- XEP-0054: vCard with optional photo + vCard fields ---
        try:
            vcard = self['xep_0054'].make_vcard()

            # Set avatar only if image data was loaded
            if image_data and avatar_type:
                vcard['PHOTO']['TYPE'] = avatar_type
                vcard['PHOTO']['BINVAL'] = image_data

            # Add optional vCard fields from config (always, regardless of avatar)
            if hasattr(config, 'VCARD_NICKNAME') and config.VCARD_NICKNAME:
                vcard['NICKNAME'] = config.VCARD_NICKNAME

            if hasattr(config, 'VCARD_FN') and config.VCARD_FN:
                vcard['FN'] = config.VCARD_FN

            if hasattr(config, 'VCARD_ORG') and config.VCARD_ORG:
                vcard['ORG']['ORGNAME'] = config.VCARD_ORG

            if hasattr(config, 'VCARD_ROLE') and config.VCARD_ROLE:
                vcard['ROLE'] = config.VCARD_ROLE

            if hasattr(config, 'VCARD_URL') and config.VCARD_URL:
                vcard['URL'] = config.VCARD_URL

            if hasattr(config, 'VCARD_NOTE') and config.VCARD_NOTE:
                vcard['NOTE'] = config.VCARD_NOTE

            await self['xep_0054'].publish_vcard(vcard)
            log.info("✅ XEP-0054 vCard updated successfully")
        except Exception as e:
            log.warning("⚠️ Failed to update XEP-0054 vCard: %s", e)

        # --- XEP-0084: User Avatar / vCard4 (only if avatar present) ---
        if image_data:
            try:
                await self['xep_0084'].publish_avatar(image_data)
                log.info("✅ XEP-0084 avatar updated successfully")
            except Exception as e:
                log.warning("⚠️ Failed to update XEP-0084 avatar: %s", e)

            # --- XEP-0153: Avatar hash in presence (only if avatar present) ---
            try:
                # SHA1 hex hash (XEP-0153 requires hex)
                sha1_hex = hashlib.sha1(image_data).hexdigest()

                # Build <x xmlns='vcard-temp:x:update'><photo>HASH</photo></x>
                x = ET.Element("{vcard-temp:x:update}x")
                photo = ET.SubElement(x, "photo")
                photo.text = sha1_hex

                await asyncio.sleep(1)

                # Build a Presence stanza and send
                presence = Presence()
                presence.append(x)
                self.send(presence)

                log.info("✅ XEP-0153 avatar hash updated successfully")
            except Exception as e:
                log.warning("⚠️ Failed to update XEP-0153 avatar: %s", e)
        else:
            log.info("ℹ️ No avatar to publish (XEP-0084, XEP-0153 skipped)")

        return True


    # ---------- MUC EVENT HANDLERS ----------
    async def muc_online(self, presence) -> None:
        """
        Called when a user comes online in a MUC.
        - Updates occupants
        - Skips admins/owners
        - AUTO-UPDATES JID IF NICK-ONLY BAN EXISTS
        - Applies all relevant bans from DB in parallel
        """
        room = presence["from"].bare
        nick = presence["muc"]["nick"]
        jid = presence["muc"].get("jid")
        jid_str = str(jid) if jid else None

        # --- Update occupants dict ---
        self.occupants.setdefault(room, {})[nick] = {
            "role": presence["muc"]["role"],
            "affiliation": presence["muc"]["affiliation"],
            "jid": jid_str,
        }

        # --- Skip admins/owners ---
        if self.is_admin_or_owner(room, nick=nick, jid=jid_str):
            return

        # --- Auto-update JID if nick-only ban exists ---
        if jid_str and nick:
            async with self.db.execute(
                "SELECT jid FROM bans WHERE LOWER(nick)=? AND (jid IS NULL OR jid='')",
                (nick.lower(),)
            ) as cursor:
                existing_ban = await cursor.fetchone()

            if existing_ban:
                # Found a nick-only ban, update it with the JID
                ban_jid_bare = self.bare_jid(jid_str)
                await self.db.execute(
                    "UPDATE bans SET jid=? WHERE LOWER(nick)=? AND (jid IS NULL OR jid='')",
                    (ban_jid_bare, nick.lower())
                )
                await self.db.commit()

                # Reload ban cache
                await self.load_bans_from_db()

                log.info("✅ Auto-updated ban for nick '%s': JID set to %s", nick, ban_jid_bare)

        # --- Fetch all bans ---
        # Use indexes for O(1) lookups instead of O(n)
        now = int(time.time())
        tasks = []

        # Check by JID
        if jid_str:
            jid_bare = self.bare_jid(jid_str)
            if jid_bare in self.ban_index_by_jid:
                ban_jid, ban_nick, until, issuer, comment = self.ban_index_by_jid[jid_bare]
                if until <= 0 or until > now:  # Check if not expired
                    tasks.append(self.apply_ban_to_room(room, ban_jid, ban_nick, comment))

            # Check by domain
            domain = jid_bare.split("@")[1].lower() if "@" in jid_bare else None
            if domain and domain in self.ban_index_by_domain:
                for ban_jid, ban_nick, until, issuer, comment in self.ban_index_by_domain[domain]:
                    if until <= 0 or until > now:
                        tasks.append(self.apply_ban_to_room(room, ban_jid, ban_nick, comment))

        # Check by nick
        if nick.lower() in self.ban_index_by_nick:
            ban_jid, ban_nick, until, issuer, comment = self.ban_index_by_nick[nick.lower()]
            if until <= 0 or until > now:
                tasks.append(self.apply_ban_to_room(room, ban_jid, ban_nick, comment))

        # --- Run all bans in parallel ---
        if tasks:
            await asyncio.gather(*tasks)

    # ---------- MUC OFFLINE ----------
    async def muc_offline(self, presence) -> None:
        """
        Called when a user goes offline in a MUC.
        - Removes them from self.occupants[room]
        - Logs offline info
        """
        room = presence["from"].bare
        nick = presence["muc"]["nick"]

        room_occ = self.occupants.get(room)
        if room_occ and nick in room_occ:
            info = room_occ.pop(nick)
            log.debug("⛔ %s went offline in %s (jid=%s, affiliation=%s, role=%s)",
                     nick,
                     room,
                     info.get("jid", "unknown"),
                     info.get("affiliation", "none"),
                     info.get("role", "none"))

    # ---------- MUC PRESENCE ----------
    async def on_muc_presence(self, presence) -> None:
        """
        Detect if bot loses or regains admin/owner rights.
        Spam-safe (only reacts on real state changes).
        """
        room = presence["from"].bare
        nick = presence["from"].resource

        if nick != NICK:
            return

        # Ignore during reconnect stabilization
        if self.reconnecting and time.time() - self.room_join_time.get(room, 0) < 5:
            return

        # Ignore first few seconds after join
        join_time = self.room_join_time.get(room)
        if join_time and (time.time() - join_time < 5):
            return

        affiliation = presence["muc"]["affiliation"]
        role = presence["muc"]["role"]

        if not affiliation:
            return

        is_admin_now = affiliation in ("admin", "owner")
        was_admin = self.bot_admin_state.get(room)

        # First time → just store
        if was_admin is None:
            self.bot_admin_state[room] = is_admin_now
            return

        if was_admin == is_admin_now:
            return

        self.bot_admin_state[room] = is_admin_now

        if not is_admin_now:
            is_admin_verified = await self.verify_admin_rights(room)
            if not is_admin_verified:
                log.warning("⚠️ Verified: Bot truly lost admin rights in %s", room)
                self.send_message(
                    mto=ADMIN_ROOM,
                    mbody=f"⚠️ Bot lost admin/owner rights in {room}\nAffiliation: {affiliation}\nRole: {role}",
                    mtype="groupchat"
                )
            else:
                log.info("✅ False alarm: server confirms bot is still admin in %s", room)
                self.bot_admin_state[room] = True  # correct state

        else:
            log.info("✅ Bot regained admin rights in %s", room)
            self.send_message(
                mto=ADMIN_ROOM,
                mbody=f"✅ Bot regained admin/owner rights in {room}",
                mtype="groupchat"
            )


    # ---------- MESSAGE HANDLER ----------
    async def on_message(self, msg) -> None:
        """
        Handles incoming messages in MUCs.
        - Ignores own messages
        - Parses commands
        - Delegates to user/admin handlers
        """
        if msg["mucnick"].lower() == NICK.lower():
            return  # Ignore own messages

        room = msg["from"].bare
        nick = msg["mucnick"]
        body = msg["body"].strip()

        if not body:
            return

        if not body.startswith(self.command_prefix):
            return

        parts = body.split()
        raw_cmd = parts[0]

        cmd = raw_cmd[len(self.command_prefix):].lower()
        args = parts[1:]

        handled = await self._handle_user_command(msg, room, nick, cmd, args)
        if handled:
            return

        await self._handle_admin_command(msg, room, nick, cmd, args)


    async def _handle_user_command(
        self,
        msg,
        room: str,
        nick: str,
        cmd: str,
        args: list[str]
    ) -> bool:
        if cmd == "help":
            if room == ADMIN_ROOM and self.is_authorized(msg):
                text = self._admin_help_text()
            elif self.user_cmds_allowed(room):
                text = self._user_help_text()
            else:
                return True

            self.send_message(mto=room, mbody=text, mtype="groupchat")
            return True

        if cmd == "banlist" and self.user_cmds_allowed(room):
            await self.cmd_banlist(room)
            return True

        if cmd == "why" and len(args) >= 1 and self.user_cmds_allowed(room):
            await self.cmd_why(args[0], room)
            return True

        if cmd == "whoami":
            await self._cmd_whoami(room, nick)
            return True

        return False


    async def _handle_admin_command(
        self,
        msg,
        room: str,
        nick: str,
        cmd: str,
        args: list[str]
    ) -> bool:
        admin_commands = {
            "config",
            "reloadconfig",
            "status",
            "checkupdate",
            "room",
            "ban",
            "tempban",
            "unban",
            "bansearch",
            "sync",
            "syncadmins",
            "syncbans",
            "export",
            "import",
        }

        if cmd not in admin_commands:
            return False

        if room != ADMIN_ROOM:
            return True

        if not self.is_authorized(msg):
            self.send_message(
                mto=room,
                mbody="❌ You are not authorized to use this admin command.",
                mtype="groupchat"
            )
            return True

        if cmd == "config":
            await self._cmd_config(room)
            return True

        if cmd == "reloadconfig":
            await self._cmd_reloadconfig(room)
            return True

        if cmd == "status":
            await self._cmd_status(room)
            return True

        if cmd == "checkupdate":
            is_update, remote_version, error_message = await self.check_for_updates_once(announce=False)

            if error_message:
                self.send_message(
                    mto=room,
                    mbody=f"❌ Update check failed: {error_message}",
                    mtype="groupchat"
                )
            elif is_update:
                self.send_message(
                    mto=room,
                    mbody=(
                        f"⬆️ New bot version available: {remote_version} (current: {__version__})\n"
                        f"Release page: {self.version_check_url}"
                    ),
                    mtype="groupchat"
                )
            else:
                self.send_message(
                    mto=room,
                    mbody=f"✅ Bot is up to date ({__version__})",
                    mtype="groupchat"
                )
            return True

        if cmd == "room":
            if len(args) >= 1:
                await self.cmd_room(args, room)
            return True

        if cmd == "ban":
            if len(args) >= 1:
                comment = " ".join(args[1:]) if len(args) > 1 else None
                await self.ban_all(args[0], None, nick, comment)
            return True

        if cmd == "tempban":
            if len(args) >= 2:
                try:
                    until = int(time.time()) + parse_duration(args[1])
                except Exception:
                    self.send_message(
                        mto=room,
                        mbody=f"❌ Invalid duration format ({self.command_prefix}tempban user 10m)",
                        mtype="groupchat"
                    )
                    return True

                comment = " ".join(args[2:]) if len(args) > 2 else None
                await self.ban_all(args[0], until, nick, comment)
            return True

        if cmd == "unban":
            if len(args) >= 1:
                await self.unban_all(args[0], nick)
            return True

        if cmd == "bansearch":
            if len(args) >= 1:
                query = " ".join(args)
                await self.cmd_bansearch(query)
            return True

        if cmd == "sync":
            await self.sync_rooms_and_bans()
            return True

        if cmd == "syncadmins":
            await self.sync_admins(announce=True)
            return True

        if cmd == "syncbans":
            await self.sync_bans()
            return True

        if cmd == "export":
            success, message = await self.export_bans_to_csv()
            self.send_message(mto=room, mbody=message, mtype="groupchat")
            return True

        if cmd == "import":
            if len(args) < 1:
                self.send_message(
                    mto=room,
                    mbody=f"❌ Usage: {self.command_prefix}import <filename>",
                    mtype="groupchat"
                )
                return True

            filename = args[0]
            successful, skipped, errors = await self.import_bans_from_csv(filename)

            result_msg = (
                f"📥 Import Results:\n"
                f"✅ Successful: {successful}\n"
                f"⚠️ Skipped: {skipped}"
            )

            if errors:
                result_msg += f"\n\n❌ Errors ({len(errors)}):\n"
                result_msg += "\n".join(errors[:10])
                if len(errors) > 10:
                    result_msg += f"\n... and {len(errors) - 10} more errors"

            self.send_message(mto=room, mbody=result_msg, mtype="groupchat")
            log.info(
                "Import completed: %d successful, %d skipped, %d errors",
                successful,
                skipped,
                len(errors)
            )
            return True

        return True


    def _user_help_text(self) -> str:
        p = self.command_prefix
        return (
            f"{p}help - show this help\n"
            f"{p}whoami - show your affiliation/role and permissions\n"
            f"{p}banlist - show temporary bans\n"
            f"{p}why <nick> - show ban reason"
        )

    def _admin_help_text(self) -> str:
        p = self.command_prefix
        return (
            f"{p}help - show this help\n"
            f"{p}config - show current configuration\n"
            f"{p}reloadconfig - reload config.py at runtime\n"
            f"{p}status - show bot health, active rooms, and ban statistics\n"
            f"{p}checkupdate - check if a newer bot release is available\n"
            f"{p}whoami - show your affiliation/role\n\n"
            f"{p}room add/remove/list - manage protected rooms\n\n"
            f"{p}ban <jid|nick> [comment] - ban user from all protected rooms\n"
            f"{p}tempban <jid|nick> <10m|2h|1d> [comment] - temporary ban\n"
            f"{p}unban <jid|nick> - remove ban\n"
            f"{p}banlist - show all active bans with remaining time and comments\n"
            f"{p}bansearch <query> - search bans by nick, domain or jid\n"
            f"{p}why <nick|jid> - show the reason and remaining time for a ban\n\n"
            f"{p}sync - rejoin rooms, verify admin rights, and enforce all active bans\n"
            f"{p}syncadmins - update admin list from the admin room\n"
            f"{p}syncbans - sync bans from all rooms into the database and enforce them\n\n"
            f"{p}export - export all bans to a CSV file\n"
            f"{p}import <filename> - import bans from a CSV file"
        )


    async def _cmd_config(self, room: str) -> None:
        config_lines = ["📋 Current Bot Configuration:\n"]

        config_lines.append(f"🤖 Bot Version: {__version__}")
        config_lines.append(f"💾 Database: {DB_FILE}")
        config_lines.append(f"🔐 JID: {JID}")
        config_lines.append(f"📦 Resource: {getattr(config, 'RESSOURCE', 'None')}")
        config_lines.append(f"👤 Nick: {NICK}")
        config_lines.append("")
        config_lines.append(f"⌨️ Command Prefix: {self.command_prefix}")
        config_lines.append(f"📢 Announce Startup: {self.announce_startup}")
        config_lines.append(f"📊 Announce Sync Details: {self.announce_sync_details}")
        config_lines.append(f"📣 Show Bans in MUC: {self.show_ban_in_muc}")
        config_lines.append(f"✅ Allow User Commands: {self.allow_user_cmds}")
        config_lines.append("")
        config_lines.append(f"⏰ Health Check Interval: {self.health_check_interval}s")
        config_lines.append(f"⏱️ Unban Check Interval: {self.unban_check_interval}s")
        config_lines.append(f"📅 Max Tempban Days: {self.max_tempban_days}")
        config_lines.append(f"🔌 MUC Write Semaphore: {self.muc_write_limit}")
        config_lines.append("")
        config_lines.append(f"🔄 Version Check Enabled: {self.version_check_enabled}")
        config_lines.append(f"🕒 Version Check Interval: {self.version_check_interval}s")
        config_lines.append(f"🌐 Version Check URL: {self.version_check_url or 'None'}")

        self.send_message(
            mto=room,
            mbody="\n".join(config_lines),
            mtype="groupchat"
        )


    async def _cmd_reloadconfig(self, room: str) -> None:
        try:
            await self.reload_runtime_config()
            self.send_message(
                mto=room,
                mbody="✅ Config reloaded successfully.",
                mtype="groupchat"
            )
            log.info("Config reloaded at runtime.")
        except Exception as e:
            self.send_message(
                mto=room,
                mbody=f"❌ Failed to reload config: {e}",
                mtype="groupchat"
            )
            log.error("Failed to reload config: %s", e)


    async def _cmd_status(self, room: str) -> None:
        status_lines = ["✅ Bot is online and healthy."]

        # version
        status_lines.append(f"🤖 Bot Version: {__version__}")
        if self.last_version_check_result:
            status_lines.append(f"🏷️ Latest Remote Version: {self.last_version_check_result}")

        # uptime
        bot_uptime = int(time.time()) - self.bot_start_time
        status_lines.append(f"\n⏱️ Bot Uptime: {human_time(bot_uptime)}")

        if self.server_connect_time:
            server_uptime = int(time.time()) - self.server_connect_time
            status_lines.append(f"🌐 Server Connected: {human_time(server_uptime)}")

        # ban count
        now = int(time.time())
        permanent_bans = 0
        temporary_bans = 0
        async with self.db.execute("SELECT until FROM bans") as cursor:
            async for (until,) in cursor:
                if until <= 0:
                    permanent_bans += 1
                elif until > now:
                    temporary_bans += 1

        status_lines.append(f"📊 Active Bans: {permanent_bans} permanent, {temporary_bans} temporary")

        # mem info
        try:
            process = psutil.Process(os.getpid())
            memory_info = process.memory_info()
            memory_mb = memory_info.rss / 1024 / 1024
            status_lines.append(f"\n💾 Memory Usage: {memory_mb:.1f} MB")
        except Exception as e:
            log.debug("Could not get memory info: %s", e)

        # cpu info
        try:
            process = psutil.Process(os.getpid())
            loop = asyncio.get_running_loop()

            # psutil samples over 1 second; run in executor so the event loop stays responsive
            cpu_percent = await loop.run_in_executor(None, process.cpu_percent, 1.0)
            cpu_load = psutil.getloadavg()[0]
            cpu_count = psutil.cpu_count()

            status_lines.append(f"🧠 CPU Usage: {cpu_percent:.1f}% (Process)")
            status_lines.append(f"⚙️ System Load: {cpu_load:.2f} ({cpu_count} cores)")
        except Exception as e:
            log.debug("Could not get CPU info: %s", e)

        # admins
        admin_infos = self.occupants.get(ADMIN_ROOM, {})
        admins = sorted(set(
            self.safe_jid(info.get("jid", "unknown"))
            for info in admin_infos.values()
            if info.get("affiliation") in ("owner", "admin")
        ))
        status_lines.append(
            "\n🛡️ Admins/Owners in Admin-Room:\n" + "\n".join(admins)
            if admins else "\n⚠️ No admins/owners found in Admin-Room."
        )

        # protected rooms
        if self.protected_rooms:
            status_lines.append(
                f"\n🔒 Protected Rooms ({len(self.protected_rooms)}):\n" +
                "\n".join(sorted(self.protected_rooms))
            )
        else:
            status_lines.append("\n⚠️ No protected rooms configured.")

        self.send_message(mto=room, mbody="\n".join(status_lines), mtype="groupchat")


    async def _cmd_whoami(self, room: str, nick: str) -> None:
        info = self.occupants.get(room, {}).get(nick, {})
        affiliation = info.get("affiliation", "none")
        role = info.get("role", "none")
        jid = info.get("jid", "unknown")

        permissions = []
        if affiliation in ("owner", "admin"):
            permissions.append("✅ Can ban/kick users")
            permissions.append("✅ Can manage room")
        elif role == "moderator":
            permissions.append("✅ Can kick users")
        else:
            permissions.append("❌ Regular participant")

        perms_text = "\n".join(permissions)

        if room == ADMIN_ROOM:
            message = (
                f"👤 **Your Status:**\n"
                f"  Nick: {nick}\n"
                f"  JID: {jid}\n"
                f"  Affiliation: {affiliation}\n"
                f"  Role: {role}\n\n"
                f"**Permissions:**\n{perms_text}"
            )
        else:
            emoji = "🔑" if affiliation in ("owner", "admin") else "👤"
            message = (
                f"{emoji} **Your Status:**\n"
                f"  Affiliation: {affiliation}\n"
                f"  Role: {role}\n\n"
                f"**Permissions:**\n{perms_text}"
            )

        self.send_message(mto=room, mbody=message, mtype="groupchat")


    # ---------- DIRECT MESSAGE HANDLER (DM/PM REJECTION) ----------
    async def on_direct_message(self, msg) -> None:
        """
        Reject direct messages (DMs/PMs) including room DMs.
        """
        # Ignore own messages
        if msg["from"].bare == self.boundjid.bare:
            return

        # Only process direct messages (type: chat or normal)
        if msg["type"] not in ("chat", "normal"):
            return

        # Get sender (bare JID for regular DMs, full JID for room DMs)
        sender = msg["from"].bare
        sender_full = str(msg["from"])

        # Determine if this is a room DM or regular DM
        is_room_dm = msg["from"].resource is not None

        # Check if sender is admin in admin room
        admin_info = self.occupants.get(ADMIN_ROOM, {})
        is_admin = False
        for nick, info in admin_info.items():
            if info.get("jid"):
                info_bare = self.bare_jid(info["jid"])
                # For room DMs, match against the sender's bare JID
                if is_room_dm and info_bare == sender:
                    if info.get("affiliation") in ("owner", "admin"):
                        is_admin = True
                        break
                # For regular DMs
                elif not is_room_dm and info_bare == sender:
                    if info.get("affiliation") in ("owner", "admin"):
                        is_admin = True
                        break

        # Send rejection response
        if is_admin:
            response = "🤖 Nice try, admin! But I'm a bot and only take commands in the admin room. See you there! 😉"
        else:
            response = "❌ I'm a ban management bot and only operate in designated rooms. I only listen to admins."

        self.send_message(
            mto=sender_full if is_room_dm else sender,
            mbody=response,
            mtype="chat"
        )


    # ---------- APPLY BAN TO ROOM ----------
    async def apply_ban_to_room(
        self,
        room: str,
        ban_jid: str | None,
        ban_nick: str | None,
        comment: str | None,
        issuer: str | None = None
    ) -> None:
        """
        Apply a ban to a room:
        - Supports JID, Nick, or Domain (*.domain.tld)
        - Sets outcast (works offline)
        - Kicks matching occupants in parallel
        - Sends notifications:
            - Admin room: full info (JID + Nick)
            - Protected rooms: only nick (anonymized)
        """
        # --- Safety: Do nothing if bot has no admin rights ---
        if not self.is_bot_admin_or_owner(room):
            log.warning("⛔ Cannot apply ban in %s (bot not admin/owner)", room)
            self.send_message(
                mto=ADMIN_ROOM,
                mbody=f"⛔ Cannot apply ban in {room} — missing admin/owner rights.",
                mtype="groupchat"
            )
            return

        is_domain = ban_jid and ban_jid.startswith("*.")
        ban_jid_bare = None if is_domain else self.bare_jid(ban_jid)
        room_occupants = self.occupants.get(room, {})

        # --- Step 1: Set Outcast (offline ban) ---
        if ban_jid_bare and not is_domain:
            for attempt in range(3):
                try:
                    async with self.muc_write_semaphore:
                        await self.plugin["xep_0045"].set_affiliation(
                            room=room,
                            jid=ban_jid_bare,
                            affiliation="outcast",
                            reason=comment or "Banned by admin"
                        )
                    log.info("✅ Outcast set for %s in %s", ban_jid_bare, room)
                    break
                except IqTimeout:
                    log.warning("Timeout setting outcast for %s in %s, retrying...", ban_jid_bare, room)
                    await asyncio.sleep(1)
                except IqError as e:
                    log.warning("IqError setting outcast for %s in %s: %s", ban_jid_bare, room, e)
                    break

        # --- Step 2: Kick matching occupants in parallel ---
        async def kick_nick(nick_name: str, info: dict) -> None:
            """Inner function to kick a single user."""
            jid_in_room = info.get("jid")
            match = False

            if is_domain and jid_in_room:
                domain = self.bare_jid(jid_in_room).split("@")[1].lower()
                match = domain == ban_jid[2:]
            elif ban_jid_bare and jid_in_room:
                match = self.bare_jid(jid_in_room) == ban_jid_bare
            elif ban_nick:
                match = nick_name.lower() == ban_nick.lower()

            if match:
                # Skip admins/owners
                if info.get("affiliation") in ("owner", "admin"):
                    log.info("❌ Skipped kick for admin/owner %s in %s", nick_name, room)
                    return

                for attempt in range(3):
                    try:
                        async with self.muc_write_semaphore:
                            await self.plugin["xep_0045"].set_role(
                                room=room,
                                nick=nick_name,
                                role="none",
                                reason=comment or "Banned by admin"
                            )
                        log.info("✅ Kicked %s from %s", nick_name, room)
                        break
                    except IqTimeout:
                        log.warning("Timeout kicking %s in %s, retrying...", nick_name, room)
                        await asyncio.sleep(1)
                    except IqError as e:
                        log.warning("IqError kicking %s in %s: %s", nick_name, room, e)
                        break

        try:
            await asyncio.gather(*(kick_nick(n, i) for n, i in room_occupants.items()))
        except Exception as e:
            log.warning("Error applying kicks: %s", e)

        # --- Step 3: Best-effort kick if nick-only not in occupants ---
        if ban_nick and ban_nick not in room_occupants:
            try:
                async with self.muc_write_semaphore:
                    await self.plugin["xep_0045"].set_role(
                        room=room,
                        nick=ban_nick,
                        role="none",
                        reason=comment or "Banned by admin"
                    )
                log.info("✅ Kick applied to %s (nick-only) in %s", ban_nick, room)
            except IqError as e:
                log.debug("Could not kick nick-only user %s: %s", ban_nick, e)
            except IqTimeout:
                log.warning("Timeout kicking nick-only user %s", ban_nick)

        # --- Step 4: Notifications ---
        if room == ADMIN_ROOM:
            display = self.bare_jid(ban_jid) if ban_jid else (ban_nick or "Unknown")
            msg_admin = f"✅ Banned {display}" + (f" ({comment})" if comment else "") + f" by {issuer}"
            self.send_message(mto=ADMIN_ROOM, mbody=msg_admin, mtype="groupchat")
        elif room in self.protected_rooms:
            display = ban_nick or "Unknown"
            msg = f"✅ Banned {display}" + (f" ({comment})" if comment else "")
            if self.allow_user_cmds and self.show_ban_in_muc:
                self.send_ephemeral(room, msg)


    # ---------- BAN ALL ----------
    async def ban_all(self, identifier: str, until: int | None, issuer: str, comment: str | None = None) -> None:
        """
        Bans a user by JID, nick, or domain (*.domain.tld):
        - Validates JID format
        - Checks tempban duration limits
        - Handles duplicate bans (smart conversion)
        - Prevents race conditions
        """
        ts = until if until is not None else 0

        ban_jid = None
        ban_nick = None
        is_domain = identifier.startswith("*.")

        # --- Step 1: Validations ---
        if is_domain:
            # Domain ban validation
            is_valid, error_msg = validate_domain_ban(identifier[2:])
            if not is_valid:
                self.send_message(mto=ADMIN_ROOM, mbody=error_msg, mtype="groupchat")
                return
            ban_jid = identifier.lower()
            ban_nick = None
        else:
            is_jid = "@" in identifier

            # JID format validation
            if is_jid and not validate_jid_format(identifier):
                self.send_message(
                    mto=ADMIN_ROOM,
                    mbody=f"❌ Invalid JID format: {identifier}. Expected: user@domain.tld",
                    mtype="groupchat"
                )
                return

            ban_jid = identifier if is_jid else None
            ban_nick = None if is_jid else identifier.lower()

            # --- Find JID if only Nick provided ---
            if ban_nick and not ban_jid:
                for room_occ in list(self.occupants.values()):
                    for n, info in list(room_occ.items()):
                        if n.lower() == ban_nick and info.get("jid"):
                            ban_jid = self.bare_jid(info["jid"])
                            break
                    if ban_jid:
                        break

            # --- Find Nick if only JID provided ---
            ban_jid_bare = self.bare_jid(ban_jid) if ban_jid else None
            if ban_jid and not ban_nick:
                for room_occ in list(self.occupants.values()):
                    for n, info in list(room_occ.items()):
                        if info.get("jid") and self.bare_jid(info["jid"]) == ban_jid_bare:
                            ban_nick = n.lower()
                            break
                    if ban_nick:
                        break

        # --- Step 2: Check tempban duration limits ---
        if until is not None and until > 0:
            max_days = self.max_tempban_days  # Uses reloadable config
            max_seconds = max_days * 86400
            duration = until - int(time.time())

            if duration <= 0:
                self.send_message(
                    mto=ADMIN_ROOM,
                    mbody=f"❌ Invalid duration: must be in the future.",
                    mtype="groupchat"
                )
                return

            if duration > max_seconds:
                self.send_message(
                    mto=ADMIN_ROOM,
                    mbody=f"❌ Tempban duration exceeds MAX_TEMPBAN_DAYS ({max_days} days). Max: {max_days} days.",
                    mtype="groupchat"
                )
                return

        # --- Step 3: Check for duplicate bans and handle smart conversion ---
        db_key = ban_jid or ban_nick
        skip_final_message = False

        if db_key in self.ban_cache:
            existing_jid, existing_nick, existing_until, existing_issuer, existing_comment = self.ban_cache[db_key]
            existing_is_permanent = existing_until <= 0
            new_is_permanent = ts <= 0

            if existing_is_permanent and new_is_permanent:
                # Permanent → Permanent
                self.send_message(
                    mto=ADMIN_ROOM,
                    mbody=f"ℹ️ Ban already exists for {identifier} (permanent)",
                    mtype="groupchat"
                )
                return
            elif existing_is_permanent and not new_is_permanent:
                # Permanent → Tempban (CONVERT)
                log.info("🔄 Converting permanent ban to tempban for %s", identifier)
                self.send_message(
                    mto=ADMIN_ROOM,
                    mbody=f"🔄 Converting permanent ban to tempban for {identifier} ({human_time(until - int(time.time()))})",
                    mtype="groupchat"
                )
                skip_final_message = True
            elif not existing_is_permanent and new_is_permanent:
                # Tempban → Permanent (CONVERT)
                log.info("🔄 Converting tempban to permanent ban for %s", identifier)
                self.send_message(
                    mto=ADMIN_ROOM,
                    mbody=f"🔄 Converting tempban to permanent ban for {identifier}",
                    mtype="groupchat"
                )
                skip_final_message = True
            else:
                # Tempban → Tempban (UPDATE)
                new_duration = human_time(until - int(time.time()))
                old_duration = human_time(max(0, existing_until - int(time.time())))
                log.info("🔄 Updating tempban for %s: %s → %s", identifier, old_duration, new_duration)
                self.send_message(
                    mto=ADMIN_ROOM,
                    mbody=f"🔄 Ban updated: {identifier}'s tempban duration changed from {old_duration} to {new_duration}",
                    mtype="groupchat"
                )
                skip_final_message = True

        # --- Prevent banning admins/owners ---
        for room_occ in list(self.occupants.values()):
            for n, info in list(room_occ.items()):
                jid_value = info.get("jid")
                info_jid_bare = self.bare_jid(jid_value) if jid_value else None
                if is_domain:
                    if info_jid_bare and info_jid_bare.split("@")[1].lower() == ban_jid[2:]:
                        if info.get("affiliation") in ("owner", "admin"):
                            self.send_message(
                                mto=ADMIN_ROOM,
                                mbody=f"❌ Refused to ban admin/owner on domain {ban_jid}: {n}",
                                mtype="groupchat"
                            )
                            return
                else:
                    if ((ban_jid_bare and info_jid_bare == ban_jid_bare) or (ban_nick and n.lower() == ban_nick)):
                        if info.get("affiliation") in ("owner", "admin"):
                            self.send_message(
                                mto=ADMIN_ROOM,
                                mbody=f"❌ Refused to ban admin/owner: {n}",
                                mtype="groupchat"
                            )
                            return

        # --- Save ban to DB (REPLACE for atomic operation) ---
        try:
            await self.db.execute(
                "REPLACE INTO bans (jid, nick, until, issuer, comment) VALUES (?, ?, ?, ?, ?)",
                (self.bare_jid(ban_jid) if ban_jid else None, ban_nick, ts, issuer, comment)
            )
            await self.db.commit()
        except Exception as e:
            log.error("Database error when saving ban: %s", e)
            self.send_message(
                mto=ADMIN_ROOM,
                mbody=f"❌ Database error: {e}",
                mtype="groupchat"
            )
            return

        self._cache_ban(ban_jid, ban_nick, ts, issuer, comment)

        log.info("Ban applied: identifier=%s, JID/Nick=%s/%s, until=%s, issuer=%s",
                 identifier, ban_jid, ban_nick, ts, issuer)

        # --- Notify Admin Room explicitly (skip if conversion message was sent) ---
        if not skip_final_message:
            display = self.bare_jid(ban_jid) if ban_jid else (ban_nick or "Unknown")
            time_info = f" ({human_time(ts - int(time.time()))})" if ts > 0 else ""
            msg_admin = f"✅ Banned {display}{time_info}" + (f" ({comment})" if comment else "") + f" by {issuer}"
            self.send_message(mto=ADMIN_ROOM, mbody=msg_admin, mtype="groupchat")

        # --- Apply ban to all protected rooms ---
        for room in self.protected_rooms:
            try:
                if is_domain:
                    # Kick all current occupants from this domain
                    for n, info in list(self.occupants.get(room, {}).items()):
                        jid_in_room = info.get("jid")
                        if jid_in_room and self.bare_jid(jid_in_room).split("@")[1].lower() == ban_jid[2:]:
                            await self.apply_ban_to_room(room, self.bare_jid(jid_in_room), n, comment, issuer)
                else:
                    await self.apply_ban_to_room(room, ban_jid, ban_nick, comment, issuer)
            except (IqError, IqTimeout) as e:
                log.warning("Failed to ban/kick %s in %s: %s", identifier, room, e)


    # ---------- UNBAN WORKER ----------
    async def unban_worker(self) -> None:
        """
        Periodically unban users whose temporary bans have expired.
        Runs in an infinite loop every 60 seconds (configurable via UNBAN_CHECK_INTERVAL).
        Improved error handling to prevent crashes.
        """
        while True:
            now = int(time.time())
            try:
                # --- Fetch expired bans (limited to 100 per check) ---
                async with self.db.execute(
                    "SELECT jid, nick FROM bans WHERE until > 0 AND until <= ? LIMIT 100", (now,)
                ) as cursor:
                    rows = await cursor.fetchall()

                expired = []
                for ban_jid, ban_nick in rows:
                    identifier = self.bare_jid(ban_jid) if ban_jid else ban_nick
                    log.info("⏳ Temporary ban expired: %s, auto-unbanning...", identifier)
                    expired.append(identifier)
                    await self.unban_all(identifier, issuer="system")

                if expired:
                    await self.load_bans_from_db()
                    log.info("✅ Auto-unbanned %d users", len(expired))

                # Check if there are more expired bans pending
                async with self.db.execute(
                    "SELECT COUNT(*) FROM bans WHERE until > 0 AND until <= ?", (now,)
                ) as cursor:
                    count_row = await cursor.fetchone()
                    if count_row and count_row[0] > 0:
                        log.debug("ℹ️ %d more expired bans pending for next cycle", count_row[0])

            except asyncio.CancelledError:
                log.info("unban_worker cancelled")
                raise
            except Exception as e:
                log.warning("Error in unban_worker: %s", e)
                await asyncio.sleep(5)
                continue

            # Configurable check interval (reloadable via !reloadconfig)
            check_interval = self.unban_check_interval
            await asyncio.sleep(check_interval)


    # ---------- APPLY UNBAN TO ROOM ----------
    async def apply_unban_to_room(
        self,
        room: str,
        ban_jid: str | None,
        ban_nick: str | None,
        domain: str | None = None
    ) -> None:
        """
        Removes Outcast for a user reliably.
        If user is online, restores participant role.
        Sends notifications according to room type and config.
        """
        try:
            # --- Step 1: Remove Outcast (works offline) ---
            if ban_jid:
                bare = self.bare_jid(ban_jid)
                for attempt in range(3):
                    try:
                        async with self.muc_write_semaphore:
                            await self.plugin["xep_0045"].set_affiliation(
                                room=room,
                                jid=bare,
                                affiliation="none"
                            )
                        log.info("✅ Outcast removed for %s in %s", bare, room)
                        break
                    except IqTimeout:
                        log.warning("Timeout removing outcast for %s in %s, retrying...", bare, room)
                        await asyncio.sleep(1)
                    except IqError as e:
                        log.debug("IqError removing outcast for %s in %s: %s", bare, room, e)
                        break

            # --- Step 2: Restore role if online ---
            room_occupants = self.occupants.get(room, {})
            for n, info in room_occupants.items():
                jid_in_room = info.get("jid")
                if ((ban_jid and jid_in_room and self.bare_jid(jid_in_room) == self.bare_jid(ban_jid)) or
                    (ban_nick and n.lower() == ban_nick) or
                    (domain and jid_in_room and jid_in_room.split("@")[-1].lower() == domain)):
                    for attempt in range(2):
                        try:
                            async with self.muc_write_semaphore:
                                await self.plugin["xep_0045"].set_role(
                                    room=room,
                                    nick=n,
                                    role="participant"
                                )
                            log.info("✅ Participant role restored for %s in %s", n, room)
                            break
                        except IqTimeout:
                            log.warning("Timeout restoring role for %s in %s, retrying...", n, room)
                            await asyncio.sleep(1)
                        except IqError as e:
                            log.debug("IqError restoring role for %s in %s: %s", n, room, e)
                            break

            # --- Step 3: Notifications ---
            if room == ADMIN_ROOM:
                display_admin = ban_jid or ban_nick or "Unknown"
                msg_admin = f"♻️ Unbanned {display_admin}"
                self.send_message(mto=ADMIN_ROOM, mbody=msg_admin, mtype="groupchat")

            elif self.allow_user_cmds:
                display = ban_nick or "Unknown"
                msg = f"♻️ Unbanned {display}"
                self.notify_protected(room, msg)

        except (IqError, IqTimeout) as e:
            log.warning("Failed to unban %s in %s: %s", ban_jid or ban_nick, room, e)


    # ---------- UNBAN HANDLING ----------
    async def unban_all(self, identifier: str, issuer: str | None = None) -> None:
        """
        Remove a ban from a user (JID, nick, or domain) and unban in all protected rooms.
        Supports domain bans (*.domain.tld)
        Admin Room: full info
        Protected Rooms: only nick/JID anonymized
        """
        if not identifier:
            return

        is_domain_ban = identifier.startswith("*.") if identifier else False
        domain = identifier[2:].lower() if is_domain_ban else None

        row = None
        is_jid = "@" in identifier

        if not is_domain_ban:
            # Lookup JID in DB
            if is_jid:
                async with self.db.execute("SELECT jid, nick FROM bans WHERE jid=?", (identifier,)) as cur:
                    row = await cur.fetchone()

            # Lookup nick in DB
            if not row:
                async with self.db.execute("SELECT jid, nick FROM bans WHERE LOWER(nick)=?", (identifier.lower(),)) as cur:
                    row = await cur.fetchone()

            # Fallback nick-only check against JIDs
            if not row and not is_jid:
                async with self.db.execute("SELECT jid, nick FROM bans") as cursor:
                    async for jid_db, nick_db in cursor:
                        if jid_db and self.bare_jid(jid_db).split("@")[0].lower() == identifier.lower():
                            row = (jid_db, nick_db)
                            break

        ban_jid = row[0] if row and row[0] else None
        ban_nick = row[1] if row and row[1] else (None if ban_jid else identifier.lower())

        # Delete from DB
        if is_domain_ban:
            await self.db.execute("DELETE FROM bans WHERE jid = ?", (identifier,))
            await self.db.execute("DELETE FROM bans WHERE jid LIKE ?", (f"%@{domain}",))
        elif ban_jid:
            await self.db.execute("DELETE FROM bans WHERE jid=? OR LOWER(nick)=?", (ban_jid, ban_nick))
        else:
            await self.db.execute("DELETE FROM bans WHERE LOWER(nick)=?", (ban_nick,))
        await self.db.commit()

        # Update in-memory cache and indexes
        if is_domain_ban and domain:
            self._remove_domain_bans_from_cache(domain)
        else:
            self._remove_ban_from_cache(identifier, ban_jid=ban_jid, ban_nick=ban_nick)

        # Unban in all protected rooms
        for room in self.protected_rooms:
            try:
                await self.apply_unban_to_room(room, ban_jid if not is_domain_ban else None, ban_nick, domain=domain if is_domain_ban else None)
            except Exception as e:
                log.warning("Error unbanning %s in %s: %s", identifier, room, e)

        # Admin Room notification
        if issuer == "system":
            msg_admin = f"♻️ Unbanned {identifier} (tempban expired)"
        else:
            msg_admin = f"♻️ Unbanned {identifier}" + (f" by {issuer}" if issuer else "")
        self.send_message(mto=ADMIN_ROOM, mbody=msg_admin, mtype="groupchat")
        log.info(msg_admin)


    # ---------- ROOM MANAGEMENT ----------

    async def validate_room_jid(self, room_jid: str) -> tuple[bool, str]:
        """
        Validate a room JID in two steps:
        1. Format validation (name@domain.tld)
        2. Service Discovery check (XEP-0030)

        Returns: (is_valid: bool, error_message: str)
        """
        room_jid = room_jid.strip().lower()

        # --- Step 1: Format Validation ---
        if not room_jid:
            return False, "❌ Room JID cannot be empty."

        if "@" not in room_jid:
            return False, "❌ Invalid JID format. Expected: name@muc.example.com"

        parts = room_jid.split("@")
        if len(parts) != 2:
            return False, "❌ Invalid JID format. Expected: name@muc.example.com"

        room_name, domain = parts

        # Check for valid characters (alphanumeric, dots, hyphens, underscores)
        if not re.match(r"^[a-z0-9._-]+$", room_name):
            return False, f"❌ Invalid room name '{room_name}'. Use alphanumeric, dots, hyphens, underscores only."

        if not re.match(r"^[a-z0-9.-]+\.[a-z]{2,}$", domain):
            return False, f"❌ Invalid domain '{domain}'. Expected: domain.tld"

        # --- Step 2: Service Discovery Check (XEP-0030) ---
        try:
            info = await self.plugin["xep_0030"].get_info(jid=room_jid, timeout=5)

            # Check if it's a MUC (Multi-User Chat)
            identities = info["disco_info"]["identities"]
            is_muc = any(
                identity[0] == "conference" and identity[1] == "text"
                for identity in identities
            )

            if not is_muc:
                return False, f"❌ '{room_jid}' exists but is not a Multi-User Chat room."

            log.info("✅ Room validated: %s (is MUC)", room_jid)
            return True, ""

        except IqTimeout:
            return False, f"❌ Service Discovery timeout for '{room_jid}'. Room may not exist or server is unresponsive."
        except IqError as e:
            error_msg = str(e.iq["error"]["type"]) if e.iq and e.iq["error"] else "Unknown error"
            return False, f"❌ Service Discovery error for '{room_jid}': {error_msg}"
        except Exception as e:
            return False, f"❌ Failed to validate room: {str(e)}"


    async def cmd_room(self, args: list[str], room: str) -> None:
        """
        Manage protected rooms.
        Commands: list, add <room>, remove <room>

        The `add` command now validates:
        - JID format (name@domain.tld)
        - Room existence via Service Discovery (XEP-0030)
        """
        if not args:
            return

        action = args[0].lower()

        if action == "list":
            if self.protected_rooms:
                rooms = sorted(self.protected_rooms)
                text = f"🔒 Protected Rooms ({len(rooms)}):\n" + "\n".join(rooms)
            else:
                text = "🔒 Protected Rooms:\nNo protected rooms."

            self.send_message(mto=room, mbody=text, mtype="groupchat")

        elif action in ("add", "remove") and len(args) >= 2:
            target = args[1].lower()

            if action == "add":
                # --- Validate Room JID before adding ---
                is_valid, error_msg = await self.validate_room_jid(target)
                if not is_valid:
                    self.send_message(mto=room, mbody=error_msg, mtype="groupchat")
                    log.warning("Room validation failed for %s: %s", target, error_msg)
                    return

                if target not in self.protected_rooms:
                    # --- In-Memory and DB ---
                    self.protected_rooms.add(target)
                    await self.db.execute("INSERT OR REPLACE INTO rooms (room) VALUES (?)", (target,))
                    await self.db.commit()
                    self.send_message(mto=room, mbody=f"✅ Room added: {target}", mtype="groupchat")

                    # --- Event handler for new occupants ---
                    if target not in self.registered_rooms:
                        self.add_event_handler(f"muc::{target}::got_online", self.muc_online)
                        self.add_event_handler(f"muc::{target}::got_offline", self.muc_offline)
                        self.registered_rooms.add(target)

                    self.plugin["xep_0045"].join_muc(target, NICK)

                    # --- Ensure the bot itself is online ---
                    async def wait_for_bot_online():
                        # Wait until the bot itself is recognized as Nick.
                        for _ in range(10):  # max 10 second timeout
                            occ = self.occupants.get(target, {})
                            if NICK in occ:
                                break
                            await asyncio.sleep(1)
                        # --- Start ban sync for this new room ---
                        await self.sync_bans_to_rooms_for_single_room(target)

                        # --- Optional: Check all other rooms for new bans ---
                        other_rooms = self.protected_rooms - {target}
                        if other_rooms:
                            log.info("🔄 Applying existing bans to other rooms due to new room addition")
                            self.send_message(
                                mto=ADMIN_ROOM,
                                mbody=f"🔄 Applying existing bans to other rooms due to new room addition",
                                mtype="groupchat"
                            )
                            for room in other_rooms:
                                await self.sync_bans_to_rooms_for_single_room(room)

                    await wait_for_bot_online()
                else:
                    self.send_message(mto=room, mbody=f"⚠️ Room already in protected list: {target}", mtype="groupchat")

            elif action == "remove":
                self.protected_rooms.discard(target)
                await self.db.execute("DELETE FROM rooms WHERE room=?", (target,))
                await self.db.commit()
                self.send_message(mto=room, mbody=f"✅ Room removed: {target}", mtype="groupchat")

                # --- Bot leaves the room immediately ---
                try:
                    self.plugin["xep_0045"].leave_muc(target, NICK)
                except Exception as e:
                    log.warning("⚠️ Failed to leave room %s: %s", target, e)


    # ---------- SYNC ----------

    async def sync_rooms_and_bans(self) -> None:
        """
        Full sync for !sync command:
        - Rejoin all protected rooms
        - Check bot admin/owner rights
        - Apply all active bans (only missing ones)
        - Skip expired temporary bans automatically
        Batches rooms to respect semaphore limits and prevent overwhelming server
        """
        now = int(time.time())
        total_rooms = len(self.protected_rooms)
        if total_rooms == 0:
            self.send_message(
                mto=ADMIN_ROOM,
                mbody="⚠️ No protected rooms to sync.",
                mtype="groupchat"
            )
            return

        async def sync_single_room(idx: int, room: str) -> None:
            # --- Leave and rejoin room to refresh presence ---
            try:
                self.plugin["xep_0045"].leave_muc(room, NICK)
                await asyncio.sleep(0.5)  # short delay
                self.plugin["xep_0045"].join_muc(room, NICK)
            except Exception as e:
                log.warning("⚠️ Failed to rejoin room %s: %s", room, e)

            self.room_join_time[room] = time.time()
            self.bot_admin_state[room] = self.is_bot_admin_or_owner(room)

            # --- Wait until bot is recognized in occupants ---
            for _ in range(10):
                occ = self.occupants.get(room, {})
                if NICK in occ:
                    break
                await asyncio.sleep(1)

            # --- Check bot admin/owner rights ---
            if not self.is_bot_admin_or_owner(room):
                log.warning("⛔ Skipping %s — bot is not admin/owner", room)
                self.send_message(
                    mto=ADMIN_ROOM,
                    mbody=f"⛔ Skipping {room} — bot has no admin/owner rights",
                    mtype="groupchat"
                )
                return

            # --- Announce start of sync ---
            self.send_message(
                mto=ADMIN_ROOM,
                mbody=f"⏳ Syncing bans in room {room} ({idx}/{total_rooms})...",
                mtype="groupchat"
            )

            # --- Fetch all active bans ---
            async with self.db.execute("SELECT jid, nick, until, comment FROM bans") as cursor:
                db_bans = await cursor.fetchall()

            active_bans = []
            for ban_jid, ban_nick, until, comment in db_bans:
                if until > 0 and until <= now:  # skip expired temporary bans
                    continue
                active_bans.append((ban_jid, ban_nick, comment))

            # --- Fetch current outcasts in this room ---
            try:
                outcasts = await self.plugin["xep_0045"].get_users_by_affiliation(room, "outcast")
                outcasts_bare = [self.bare_jid(str(j)) for j in outcasts]
            except Exception as e:
                log.warning("⚠️ Failed to fetch outcasts for %s: %s", room, e)
                outcasts_bare = []

            # --- Apply only MISSING bans ---
            tasks = []
            new_bans_count = 0

            for ban_jid, ban_nick, comment in active_bans:
                # Check if already outcast in this room
                already_banned = False

                if ban_jid:
                    ban_jid_bare = self.bare_jid(ban_jid)
                    if ban_jid_bare in outcasts_bare:
                        already_banned = True
                        log.debug("✓ %s already banned in %s, skipping", ban_jid_bare, room)

                if not already_banned:
                    tasks.append(self.apply_ban_to_room(room, ban_jid, ban_nick, comment))
                    new_bans_count += 1

            if tasks:
                await asyncio.gather(*tasks)
                log.info("ℹ️ Applied %d new bans in %s (skipped %d already banned)",
                        new_bans_count, room, len(active_bans) - new_bans_count)
            else:
                log.info("ℹ️ All bans already applied in %s, nothing to do", room)

            self.send_message(
                mto=ADMIN_ROOM,
                mbody=f"✅ Finished syncing room {room} ({idx}/{total_rooms}) - {new_bans_count} new bans applied",
                mtype="groupchat"
            )

        # Batch rooms to prevent overwhelming server
        batch_size = 10  # Hardcoded: sync 10 rooms at a time
        rooms_list = list(self.protected_rooms)

        for batch_num in range(0, len(rooms_list), batch_size):
            batch = rooms_list[batch_num:batch_num + batch_size]
            batch_start = batch_num + 1
            batch_end = min(batch_num + batch_size, len(rooms_list))

            self.send_message(
                mto=ADMIN_ROOM,
                mbody=f"⏳ Syncing batch {batch_start}-{batch_end}/{total_rooms}...",
                mtype="groupchat"
            )

            # Run batch in parallel
            try:
                await asyncio.gather(*(
                    sync_single_room(batch_num + 1 + i, room)
                    for i, room in enumerate(batch)
                ))
            except Exception as e:
                log.warning("Error in batch %d-%d: %s", batch_start, batch_end, e)
                self.send_message(
                    mto=ADMIN_ROOM,
                    mbody=f"⚠️ Error during batch {batch_start}-{batch_end} sync: {e}",
                    mtype="groupchat"
                )

            # Small delay between batches to avoid overwhelming server
            if batch_end < len(rooms_list):
                await asyncio.sleep(1)

        log.info("✅ Full !sync completed for %d rooms", total_rooms)
        self.send_message(
            mto=ADMIN_ROOM,
            mbody=f"✅ Full !sync completed for {total_rooms} rooms in {(len(rooms_list) + batch_size - 1) // batch_size} batches",
            mtype="groupchat"
        )


    async def sync_bans_to_rooms_for_single_room(self, room: str) -> None:
        """
        Sync bans for a single room (after !room add or !sync).
        Skips expired temporary bans automatically.
        Only applies bans that are NOT already set (outcast affiliation).
        """
        if not self.is_bot_admin_or_owner(room):
            log.warning("⛔ Skipping initial sync for %s (bot is not admin/owner)", room)
            self.send_message(
                mto=ADMIN_ROOM,
                mbody=f"⛔ Cannot sync {room} — bot has no admin/owner rights.",
                mtype="groupchat"
            )
            return

        try:
            now = int(time.time())
            issuer_tag = "sync_room_add"

            # --- Load all bans from DB ---
            async with self.db.execute("SELECT jid, nick, until, comment FROM bans") as cursor:
                db_bans = await cursor.fetchall()

            # --- Remove expired temporary bans from consideration ---
            active_bans = []
            for ban_jid, ban_nick, until, comment in db_bans:
                if until > 0 and until <= now:
                    continue  # Skip expired temporary bans
                active_bans.append((ban_jid, ban_nick, until, comment))

            # --- Fetch current outcasts in the room ---
            try:
                outcasts = await self.plugin["xep_0045"].get_users_by_affiliation(room, "outcast")
                outcasts_bare = [self.bare_jid(str(j)) for j in outcasts]
            except Exception as e:
                log.warning("⚠️ Failed to fetch outcasts for %s: %s", room, e)
                outcasts_bare = []

            # --- Add orphan outcasts to DB ---
            to_insert = []
            for jid_bare in outcasts_bare:
                if not any(ban_jid and self.bare_jid(ban_jid) == jid_bare for ban_jid, _, _, _ in active_bans):
                    to_insert.append((jid_bare, None, 0, issuer_tag, "Recovered from room"))
                    active_bans.append((jid_bare, None, 0, issuer_tag))

            if to_insert:
                await self.db.executemany(
                    "INSERT INTO bans (jid, nick, until, issuer, comment) VALUES (?, ?, ?, ?, ?)",
                    to_insert
                )
                await self.db.commit()
                log.info("✅ Added %d orphan outcasts to DB for room %s", len(to_insert), room)

            # --- Apply only MISSING bans in this room ---
            tasks = []
            new_bans_count = 0

            for ban_jid, ban_nick, until, comment in active_bans:
                # Check if already outcast in this room
                already_banned = False

                if ban_jid:
                    ban_jid_bare = self.bare_jid(ban_jid)
                    if ban_jid_bare in outcasts_bare:
                        already_banned = True
                        log.debug("✓ %s already banned in %s, skipping", ban_jid_bare, room)

                if not already_banned:
                    tasks.append(self.apply_ban_to_room(room, ban_jid, ban_nick, comment))
                    new_bans_count += 1

            if tasks:
                await asyncio.gather(*tasks)
                log.info("ℹ️ Applied %d new bans in %s (skipped %d already banned)",
                        new_bans_count, room, len(active_bans) - new_bans_count)
            else:
                log.info("ℹ️ All bans already applied in %s, nothing to do", room)

            log.info("✅ Ban sync completed for room %s (%d new bans applied)", room, new_bans_count)

        except Exception as e:
            log.warning("⚠️ Failed to sync bans for room %s: %s", room, e)


    async def sync_admins(self, announce: bool = False) -> None:
        """
        Fetch current owners/admins from ADMIN_ROOM via XMPP.
        Updates self.occupants for admin checks.
        If announce=True, sends list to ADMIN_ROOM.
        """
        room = ADMIN_ROOM
        try:
            owners = await self.plugin["xep_0045"].get_users_by_affiliation(room, "owner")
            admins = await self.plugin["xep_0045"].get_users_by_affiliation(room, "admin")

            self.occupants[room] = self.occupants.get(room, {})
            admin_list = []
            admin_log_list = []

            for jid in owners + admins:
                bare = self.bare_jid(str(jid))
                nick = None

                for n, info in self.occupants.get(room, {}).items():
                    if info.get("jid") and self.bare_jid(info["jid"]) == bare:
                        nick = n
                        break

                aff = "owner" if jid in owners else "admin"
                self.occupants[room][nick or bare] = {
                    "role": "moderator" if nick else "participant",
                    "affiliation": aff,
                    "jid": bare,
                }

                admin_list.append(self.safe_jid(bare))
                admin_log_list.append(bare)

            log.info("Admins synced: %s", ", ".join(admin_log_list))

            if announce:
                if admin_list:
                    msg = "✅ Current admins/owners in Admin-Room:\n" + "\n".join(admin_list)
                else:
                    msg = "⚠️ No admins/owners found in Admin-Room."

                self.send_message(mto=ADMIN_ROOM, mbody=msg, mtype="groupchat")

        except Exception as e:
            log.warning("Failed to sync admins: %s", e)


    async def sync_bans_to_rooms(self, startup: bool = False, announce_progress: bool = True) -> None:
        """
        Sync all bans from the database to all protected rooms.
        Skips expired temporary bans.
        Only applies bans that are NOT already set (outcast affiliation).
        Counts only unique bans for statistics.

        :param startup: If True, this is called at startup.
        :param announce_progress: Send progress messages to ADMIN_ROOM.
        """
        if not self.protected_rooms:
            if announce_progress:
                self.send_message(
                    mto=ADMIN_ROOM,
                    mbody="⚠️ No protected rooms configured for ban sync.",
                    mtype="groupchat"
                )
            return

        now = int(time.time())
        applied_bans_set: set[tuple[str | None, str | None]] = set()

        # --- Load all bans from DB ---
        async with self.db.execute("SELECT jid, nick, until, comment FROM bans") as cursor:
            db_bans = await cursor.fetchall()

        # --- Filter active bans ---
        active_bans = [
            (ban_jid, ban_nick, comment)
            for ban_jid, ban_nick, until, comment in db_bans
            if until == 0 or until > now
        ]

        if not active_bans and announce_progress:
            self.send_message(
                mto=ADMIN_ROOM,
                mbody="✅ No active bans to sync.",
                mtype="groupchat"
            )
            return

        # --- Apply bans to each protected room ---
        for idx, room in enumerate(self.protected_rooms, start=1):
            if announce_progress:
                self.send_message(
                    mto=ADMIN_ROOM,
                    mbody=f"⏳ Syncing bans in room {room} ({idx}/{len(self.protected_rooms)})...",
                    mtype="groupchat"
                )

            # Skip room if bot not admin/owner
            if not self.is_bot_admin_or_owner(room):
                log.warning("⛔ Skipping %s — bot not admin/owner", room)
                if announce_progress:
                    self.send_message(
                        mto=ADMIN_ROOM,
                        mbody=f"⛔ Skipping {room} — bot has no admin/owner rights",
                        mtype="groupchat"
                    )
                continue

            # --- Fetch current outcasts ---
            try:
                outcasts = await self.plugin["xep_0045"].get_users_by_affiliation(room, "outcast")
                outcasts_bare = [self.bare_jid(str(j)) for j in outcasts]
            except Exception as e:
                log.warning("⚠️ Failed to fetch outcasts for %s: %s", room, e)
                outcasts_bare = []

            # --- Add orphan outcasts to DB ---
            orphan_bans = []
            for jid_bare in outcasts_bare:
                if not any(ban_jid and self.bare_jid(ban_jid) == jid_bare for ban_jid, _, _ in active_bans):
                    orphan_bans.append((jid_bare, None, "Recovered from room"))

            if orphan_bans:
                await self.db.executemany(
                    "INSERT INTO bans (jid, nick, until, issuer, comment) VALUES (?, ?, 0, ?, ?)",
                    [(self.bare_jid(jid) if jid else None, nick, "sync_room_add", comment) for jid, nick, comment in orphan_bans]
                )
                await self.db.commit()
                active_bans.extend(orphan_bans)
                log.info("✅ Added %d orphan outcasts to DB for room %s", len(orphan_bans), room)

            # --- Apply only MISSING bans in parallel ---
            tasks = []
            new_bans_count = 0

            for ban_jid, ban_nick, comment in active_bans:
                # Check if already outcast in this room
                already_banned = False

                if ban_jid:
                    ban_jid_bare = self.bare_jid(ban_jid)
                    if ban_jid_bare in outcasts_bare:
                        already_banned = True
                        log.debug("✓ %s already banned in %s, skipping", ban_jid_bare, room)

                if not already_banned:
                    tasks.append(self.apply_ban_to_room(room, ban_jid, ban_nick, comment))
                    applied_bans_set.add((ban_jid, ban_nick))
                    new_bans_count += 1

            if tasks:
                await asyncio.gather(*tasks)
                log.info("ℹ️ Applied %d new bans in %s (skipped %d already banned)",
                        new_bans_count, room, len(active_bans) - new_bans_count)
            else:
                log.info("ℹ️ All bans already applied in %s, nothing to do", room)

            if announce_progress:
                self.send_message(
                    mto=ADMIN_ROOM,
                    mbody=f"✅ Finished syncing room {room} ({idx}/{len(self.protected_rooms)}) - {new_bans_count} new bans applied",
                    mtype="groupchat"
                )

        # --- Final statistics ---
        if announce_progress:
            self.send_message(
                mto=ADMIN_ROOM,
                mbody=f"✅ Startup ban sync completed: {len(applied_bans_set)} unique bans applied in {len(self.protected_rooms)} rooms",
                mtype="groupchat"
            )

        log.info("✅ Ban sync completed: %d unique bans applied in %d rooms", len(applied_bans_set), len(self.protected_rooms))


    async def sync_bans_startup(self) -> None:
        """
        Startup ban sync.
        Announce messages in Admin-Room only if ANNOUNCE_STARTUP=True in config.py
        """
        announce = (
            getattr(self, "announce_startup", True)
            and getattr(self, "announce_sync_details", True)
        )
        await self.sync_bans_to_rooms(startup=True, announce_progress=announce)


    async def sync_bans(self) -> None:
        await self.sync_bans_to_rooms(startup=False, announce_progress=True)


    # ---------- BANSEARCH ----------
    def _format_ban_match(self, jid, nick, until, issuer, comment, now):
        """Format a ban match for display in bansearch results."""
        remaining = human_time(max(0, until - now)) if until > 0 else "permanent"
        emoji = "⏳" if until > 0 else "🔒"
        return f"{emoji} {jid or nick or 'Unknown'} ({remaining}, by {issuer}" + (f", {comment}" if comment else "") + ")"

    async def cmd_bansearch(self, query: str) -> None:
        """
        Searches bans by nick, JID, or domain.
        Uses indexes for instant lookups
        """
        q = query.lower()
        matches = []
        now = int(time.time())

        # Direct index lookup first (instant!)
        if q in self.ban_index_by_jid:
            ban = self.ban_index_by_jid[q]
            matches.append(self._format_ban_match(*ban, now))

        if q in self.ban_index_by_nick:
            ban = self.ban_index_by_nick[q]
            matches.append(self._format_ban_match(*ban, now))

        if q in self.ban_index_by_domain:
            for ban in self.ban_index_by_domain[q]:
                matches.append(self._format_ban_match(*ban, now))

        # Fallback: Partial search in cache (only if no direct match)
        if not matches:
            for key, (jid, nick, until, issuer, comment) in self.ban_cache.items():
                if jid and jid.startswith("*."):
                    display = jid
                    domain = jid[2:].lower()
                    haystack = domain
                else:
                    display = jid or nick or "Unknown"
                    haystack = " ".join(filter(None, [jid, nick])).lower()

                if q in haystack:
                    matches.append(self._format_ban_match(jid, nick, until, issuer, comment, now))

        if matches:
            msg = "🔍 Ban search results:\n" + "\n".join(matches)
        else:
            msg = f"❌ No bans found matching '{query}'."

        self.send_message(
            mto=ADMIN_ROOM,
            mbody=msg,
            mtype="groupchat"
        )

    # ---------- BANLIST ----------
    async def cmd_banlist(self, room: str) -> None:
        """
        Shows active bans.
        Admin Room: full info (JID/nick/domain)
        Protected Rooms: only temporary bans, nick or domain only
        """
        async with self.db.execute("SELECT jid, nick, until, issuer, comment FROM bans") as cursor:
            rows = await cursor.fetchall()

        if not rows:
            text = "📋 Banlist:\nNo active bans."
        else:
            now = int(time.time())
            entries = []

            for jid, nick, until, issuer, comment in rows:
                # Skip permanent bans in protected rooms
                if room != ADMIN_ROOM and until <= 0:
                    continue

                remaining = human_time(max(0, until - now)) if until > 0 else "permanent"
                emoji = "⏳" if until > 0 else "🔒"

                # Domain-ban display
                if jid and jid.startswith("*."):
                    display = jid
                elif room == ADMIN_ROOM:
                    display = jid or nick or "Unknown"
                else:
                    display = nick or (jid.split("@")[0] if jid else "Unknown")

                entry = f"{emoji} {display} ({remaining}, by {issuer}" + (f", {comment}" if comment else "") + ")"
                entries.append(entry)

            if entries:
                text = f"📋 Banlist ({len(entries)}):\n" + "\n".join(entries)
            else:
                text = "📋 Banlist:\nNo active temporary bans."

        if room != ADMIN_ROOM:
            self.send_ephemeral(room, text)
        else:
            self.send_message(mto=room, mbody=text, mtype="groupchat")

    # ---------- WHY ----------
    async def cmd_why(self, identifier: str, room: str) -> None:
        """
        Show reason for a ban.
        Admin Room: full info (JID/nick)
        Protected Rooms: only nick, JID anonymized
        """
        is_jid = "@" in identifier
        ban_jid = identifier if is_jid else None
        ban_nick = None if is_jid else identifier.lower()
        row = None

        # Check JID
        if ban_jid:
            async with self.db.execute(
                "SELECT jid, nick, until, issuer, comment FROM bans WHERE jid=?", (ban_jid,)
            ) as cursor:
                row = await cursor.fetchone()

        # Check nick
        if not row:
            async with self.db.execute(
                "SELECT jid, nick, until, issuer, comment FROM bans WHERE LOWER(nick)=?", (ban_nick,)
            ) as cursor:
                row = await cursor.fetchone()

        # Fallback nick-only check against JIDs
        if not row and ban_nick:
            async with self.db.execute("SELECT jid, nick, until, issuer, comment FROM bans") as cursor:
                async for jid_db, nick_db, until, issuer, comment in cursor:
                    if jid_db and self.bare_jid(jid_db).split("@")[0].lower() == ban_nick:
                        row = (jid_db, nick_db, until, issuer, comment)
                        break

        if row:
            jid_db, nick_db, until, issuer, comment = row
            now = int(time.time())
            remaining = human_time(max(0, until - now)) if until > 0 else "permanent"
            emoji = "⏳" if until > 0 else "🔒"

            if room == ADMIN_ROOM:
                display = jid_db or nick_db or identifier
            else:
                display = nick_db or (jid_db.split("@")[0] if jid_db else identifier)

            msg = f"{emoji} {display} ({remaining}, by {issuer}" + (f", {comment}" if comment else "") + ")"
        else:
            msg = f"No ban found for {identifier}"

        if room != ADMIN_ROOM:
            self.send_ephemeral(room, msg)
        else:
            self.send_message(mto=room, mbody=msg, mtype="groupchat")


# ---------- RUN BOT ----------
if __name__ == "__main__":
    """
    Entry point for the BanBot.
    Connects to XMPP server and starts the event loop.
    Handles KeyboardInterrupt gracefully.
    """
    resource = getattr(config, "RESSOURCE", None)
    xmpp = BanBot(JID, PASSWORD, resource)

    # Attempt connection
    if xmpp.connect():
        log.info("Connected successfully. Starting event loop...")

        try:
            # Run the slixmpp event loop forever
            xmpp.loop.run_forever()
        except KeyboardInterrupt:
            # Graceful shutdown on Ctrl+C
            log.info("Bot stopped manually.")

            if xmpp.db:
                xmpp.loop.run_until_complete(xmpp.db.close())

            xmpp.disconnect()
    else:
        log.error("Unable to connect to XMPP server.")
