#!/usr/bin/env python3

# muc_banbot: XMPP Multi-Room Ban Management Bot
# Author: creme <xmpp:creme@envs.net>
# License: MIT

import os
import config
import asyncio
import logging
import time
import aiosqlite
import slixmpp
import pathlib
import importlib
import hashlib
import re
from slixmpp import ClientXMPP
from slixmpp.exceptions import IqError, IqTimeout
from slixmpp.xmlstream import ET
from slixmpp.stanza.presence import Presence
from slixmpp.plugins.xep_0054 import VCardTemp

from config import JID, PASSWORD, ADMIN_ROOM, NICK, DB_FILE

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

# ---------- TIME HELPERS ----------
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

def validate_tempban_duration(max_days: int) -> tuple[bool, str]:
    """Validate if duration doesn't exceed MAX_TEMPBAN_DAYS."""
    pass  # Used in ban_all()

# ---------- BAN BOT ----------
class BanBot(ClientXMPP):
    def __init__(self, jid: str, password: str):
        """
        Initialize the bot with:
        - Connection info (jid/password)
        - RAM caches for bans and admin state
        - Semaphores to avoid flooding XMPP server
        - Uptime tracking for bot and server connection
        Sets up DB, protected rooms, occupants dicts, and registers XMPP plugins.
        """
        super().__init__(jid, password)
        self.db: aiosqlite.Connection | None = None

        # --- Concurrency limit for MUC write operations ---
        # Prevents flooding the XMPP server with too many IQ stanzas at once
        self.muc_write_semaphore = asyncio.Semaphore(5)

        # Ban Cache: key -> (jid_bare, nick, until, issuer, comment)
        self.ban_cache: dict[str, tuple[str | None, str | None, int, str | None, str | None]] = {}

        self.bot_admin_state: dict[str, bool] = {}
        self.occupants: dict[str, dict] = {}
        self.protected_rooms: set[str] = set()
        self.registered_rooms: set[str] = set()
        self.room_join_time = {}
        self.reconnecting = False
        self.health_check_task = None

        # --- Uptime tracking ---
        self.bot_start_time = time.time()
        self.server_connect_time = None

        # --- default config options ---
        self.announce_startup: bool = getattr(config, "ANNOUNCE_STARTUP", True)
        self.show_ban_in_muc: bool = getattr(config, "SHOW_BAN_IN_MUC", True)
        self.allow_user_cmds: bool = getattr(config, "ALLOW_USER_COMMANDS_IN_PROTECTED_ROOMS", True)
        self.health_check_interval: int = max(60, getattr(config, "HEALTH_CHECK_INTERVAL", 300))
        self.unban_check_interval: int = getattr(config, "UNBAN_CHECK_INTERVAL", 60)
        self.max_tempban_days: int = max(1, min(365, getattr(config, "MAX_TEMPBAN_DAYS", 30)))

        # --- Register XMPP plugins ---
        self.register_plugin("xep_0030")  # Service Discovery
        self.register_plugin("xep_0045")  # Multi-User Chat
        self.register_plugin('xep_0054')  # vCard
        self.register_plugin('xep_0084')  # Modern Avatar
        self.register_plugin('xep_0153')  # vCard Avatar compatibility

        # --- Event handlers ---
        self.add_event_handler("session_start", self.start)
        self.add_event_handler("groupchat_message", self.on_message)
        self.add_event_handler("groupchat_presence", self.on_muc_presence)
        self.add_event_handler("disconnected", self.on_disconnect)
        self.add_event_handler("connection_failed", self.on_disconnect)

    # ---------- ADMIN / OWNER PROTECTION ----------
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

    # ---------- AUTH ----------
    def is_authorized(self, msg) -> bool:
        """
        Check if a message sender is authorized to issue admin commands.
        """
        if msg["from"].bare != ADMIN_ROOM:
            return False
        info = self.occupants.get(ADMIN_ROOM, {}).get(msg["mucnick"])
        return info and info.get("affiliation") in ("owner", "admin")

    # ---------- EPHEMERAL MESSAGE ----------
    def send_ephemeral(self, mto: str, mbody: str) -> None:
        """Send a message to a room without storing it."""
        msg = self.Message()
        msg["to"] = mto
        msg["type"] = "groupchat"
        msg["body"] = mbody
        no_store = ET.Element("{urn:xmpp:hints}no-store")
        msg.append(no_store)
        msg.send()

    # ---------- COMMAND HELPERS ----------
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

    # ---------- VCARD HELPER ----------
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
                vcard['ORG'] = config.VCARD_ORG

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
        await self.setup_db()
        await self.load_bans_from_db()

        if self.reconnecting:
            log.info("🔄 Reconnected successfully")
        else:
            # First connection (not a reconnect)
            self.bot_start_time = time.time()

        self.send_presence()
        await self.get_roster()

        await asyncio.sleep(3)

        # --- Record server connection time ---
        self.server_connect_time = time.time()

        # --- Join admin room ---
        self.plugin["xep_0045"].join_muc(ADMIN_ROOM, NICK)
        self.room_join_time[ADMIN_ROOM] = time.time()
        self.add_event_handler(f"muc::{ADMIN_ROOM}::got_online", self.muc_online)
        self.add_event_handler(f"muc::{ADMIN_ROOM}::got_offline", self.muc_offline)

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
        asyncio.create_task(self.unban_worker())

        # --- Start health check worker ---
        self.health_check_task = asyncio.create_task(self.health_check_worker())

        # --- Set Bot vCard ---
        await self.update_vcard()

        self.reconnecting = False

        log.info("✅ Bot started, all rooms joined and bans applied")

    # ---------- HEALTH CHECK ----------
    async def health_check_worker(self) -> None:
        """
        Periodically check connection status of all protected rooms.
        Verifies bot is still in rooms and has admin rights.
        Uses self.health_check_interval (reloadable via !reloadconfig).
        """
        check_interval = self.health_check_interval

        while True:
            try:
                await asyncio.sleep(check_interval)

                for room in self.protected_rooms:
                    try:
                        # Check if bot is still in room
                        occ = self.occupants.get(room, {})
                        if NICK not in occ:
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

            except Exception as e:
                log.warning("Error in health_check_worker: %s", e)
                await asyncio.sleep(10)

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
        now = int(time.time())
        bans = list(self.ban_cache.values())

        # --- Prepare tasks for relevant bans ---
        tasks = []
        for ban_jid, ban_nick, until, issuer, comment in bans:
            # skip expired
            if until > 0 and until <= now:
                continue

            match_jid = ban_jid and jid_str and self.bare_jid(jid_str) == self.bare_jid(ban_jid)
            match_nick = ban_nick and nick.lower() == ban_nick

            if match_jid or match_nick:
                # pass full comment -> apply_ban_to_room uses comment only
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
                self.bot_admin_state[room] = True  # korrigiere State

        else:
            log.info("✅ Bot regained admin rights in %s", room)
            self.send_message(
                mto=ADMIN_ROOM,
                mbody=f"✅ Bot regained admin/owner rights in {room}",
                mtype="groupchat"
            )

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

    # ---------- MESSAGE HANDLER ----------
    async def on_message(self, msg) -> None:
        """
        Handles incoming messages in MUCs.
        - Ignores own messages
        - Parses commands
        - Distinguishes admin-only commands vs. user commands
        """
        if msg["mucnick"] == NICK:
            return  # Ignore own messages

        room = msg["from"].bare
        nick = msg["mucnick"]
        body = msg["body"].strip()
        parts = body.split()
        cmd = parts[0] if parts else ""

        # ---------- HELP COMMAND ----------
        if cmd == "!help":
            if room == ADMIN_ROOM and self.is_authorized(msg):
                text = (
                    "!help - show this help\n"
                    "!reloadconfig - reload config.py at runtime\n"
                    "!status - show bot health, active rooms, and ban statistics\n"
                    "!config - show current configuration\n"
                    "!whoami - show your affiliation/role\n\n"

                    "!room add/remove/list - manage protected rooms\n\n"

                    "!ban <jid|nick> [comment] - ban user from all protected rooms\n"
                    "!tempban <jid|nick> <10m|2h|1d> [comment] - temporary ban\n"
                    "!unban <jid|nick> - remove ban\n"
                    "!banlist - show all active bans with remaining time and comments\n"
                    "!bansearch <query> - search bans by nick, domain or jid\n"
                    "!why <nick|jid> - show the reason and remaining time for a ban\n\n"

                    "!sync - rejoin rooms, verify admin rights, and enforce all active bans\n"
                    "!syncadmins - update admin list from the admin room\n"
                    "!syncbans - sync bans from all rooms into the database and enforce them"
                )
            elif self.user_cmds_allowed(room):
                text = "!help - show this help\n!banlist - show temporary bans\n!why <nick> - show ban reason"
            else:
                return
            self.send_message(mto=room, mbody=text, mtype="groupchat")
            return

        # ---------- BANLIST COMMAND ----------
        if cmd == "!banlist" and self.user_cmds_allowed(room):
            await self.cmd_banlist(room)
            return

        # ---------- WHY COMMAND ----------
        if cmd == "!why" and len(parts) >= 2 and self.user_cmds_allowed(room):
            await self.cmd_why(parts[1], room)
            return

        # ---------- ADMIN COMMANDS ----------
        admin_commands = (
            "!ban", "!tempban", "!unban", "!bansearch", "!room", "!sync",
            "!syncadmins", "!syncbans", "!status", "!whoami", "!reloadconfig", "!config"
        )
        if cmd in admin_commands:
            if room != ADMIN_ROOM or not self.is_authorized(msg):
                return

            if cmd == "!ban" and len(parts) >= 2:
                comment = " ".join(parts[2:]) if len(parts) > 2 else None
                await self.ban_all(parts[1], None, nick, comment)

            elif cmd == "!tempban" and len(parts) >= 3:
                try:
                    until = int(time.time()) + parse_duration(parts[2])
                except Exception:
                    self.send_message(
                        mto=room,
                        mbody="❌ Invalid duration format (10m, 2h, 1d).",
                        mtype="groupchat"
                    )
                    return
                comment = " ".join(parts[3:]) if len(parts) > 3 else None
                await self.ban_all(parts[1], until, nick, comment)

            elif cmd == "!unban" and len(parts) >= 2:
                await self.unban_all(parts[1], nick)

            elif cmd == "!bansearch" and len(parts) >= 2:
                query = " ".join(parts[1:])
                await self.cmd_bansearch(query)

            elif cmd == "!room" and len(parts) >= 2:
                await self.cmd_room(parts[1:], room)

            elif cmd == "!sync":
                if room != ADMIN_ROOM or not self.is_authorized(msg):
                    return

                await self.sync_rooms_and_bans()

            elif cmd == "!syncadmins":
                await self.sync_admins(announce=True)

            elif cmd == "!syncbans":
                await self.sync_bans()

            elif cmd == "!reloadconfig":
                try:
                    importlib.reload(config)

                    self.announce_startup = getattr(config, "ANNOUNCE_STARTUP", True)
                    self.show_ban_in_muc = getattr(config, "SHOW_BAN_IN_MUC", True)
                    self.allow_user_cmds = getattr(config, "ALLOW_USER_COMMANDS_IN_PROTECTED_ROOMS", True)
                    self.health_check_interval = max(60, getattr(config, "HEALTH_CHECK_INTERVAL", 300))
                    self.unban_check_interval = getattr(config, "UNBAN_CHECK_INTERVAL", 60)
                    self.max_tempban_days = max(1, min(365, getattr(config, "MAX_TEMPBAN_DAYS", 30)))

                    await self.update_vcard()

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

            elif cmd == "!status":
                status_lines = ["✅ Bot is online and healthy."]

                # Bot Uptime
                bot_uptime = int(time.time()) - self.bot_start_time
                status_lines.append(f"⏱️ Bot Uptime: {human_time(bot_uptime)}")

                # Server Connection Uptime
                if self.server_connect_time:
                    server_uptime = int(time.time()) - self.server_connect_time
                    status_lines.append(f"🌐 Server Connected: {human_time(server_uptime)}")

                # Active Bans Count
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

                admin_infos = self.occupants.get(ADMIN_ROOM, {})
                admins = [
                    f"{n} ({info['jid']})"
                    for n, info in admin_infos.items()
                    if info.get("affiliation") in ("owner", "admin")
                ]
                status_lines.append(
                    "🛡️ Admins/Owners in Admin-Room:\n" + "\n".join(admins)
                    if admins else "⚠️ No admins/owners found in Admin-Room."
                )
                status_lines.append(
                    "🔒 Protected Rooms:\n" + "\n".join(self.protected_rooms)
                    if self.protected_rooms else "⚠️ No protected rooms configured."
                )
                self.send_message(mto=room, mbody="\n".join(status_lines), mtype="groupchat")

            elif cmd == "!config":
                config_lines = ["📋 Current Bot Configuration:\n"]

                config_lines.append(f"🔐 JID: {JID}")
                config_lines.append(f"👤 Nick: {NICK}")
                config_lines.append(f"💾 Database: {DB_FILE}")
                config_lines.append(f"⏱️ Unban Check Interval: {getattr(config, 'UNBAN_CHECK_INTERVAL', 60)}s")
                config_lines.append(f"⏰ Health Check Interval: {getattr(config, 'HEALTH_CHECK_INTERVAL', 300)}s")
                config_lines.append(f"📢 Announce Startup: {self.announce_startup}")
                config_lines.append(f"📣 Show Bans in MUC: {self.show_ban_in_muc}")
                config_lines.append(f"✅ Allow User Commands: {self.allow_user_cmds}")
                config_lines.append(f"📅 Max Tempban Days: {getattr(config, 'MAX_TEMPBAN_DAYS', 30)}")

                self.send_message(
                    mto=room,
                    mbody="\n".join(config_lines),
                    mtype="groupchat"
                )

            elif cmd == "!whoami":
                info = self.occupants.get(room, {}).get(nick, {})
                self.send_message(mto=room, mbody=f"You are {info.get('affiliation', 'none')}", mtype="groupchat")

    # ---------- CHECKS ----------
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

    # ---------- HELPER FUNCTIONS ----------
    @staticmethod
    def bare_jid(jid: str | None) -> str | None:
        """
        Return the bare JID (without resource)
        e.g., user@server/resource -> user@server
        """
        return jid.split("/")[0].lower() if jid else None

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

    # ---------- BAN CACHE ----------
    async def load_bans_from_db(self) -> None:
        """
        Load all bans from the database into RAM cache.
        """
        async with self.db.execute(
            "SELECT jid, nick, until, issuer, comment FROM bans"
        ) as cursor:
            rows = await cursor.fetchall()

        # Reset cache
        self.ban_cache.clear()

        for jid, nick, until, issuer, comment in rows:
            key = jid or nick
            if key:
                # store full tuple
                self.ban_cache[key] = (jid, nick, until, issuer, comment)

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
            display = ban_jid or ban_nick or "Unknown"
            msg = f"✅ Banned {display}" + (f" ({comment})" if comment else "") + (f" by {issuer}" if issuer else "")
            self.send_message(mto=ADMIN_ROOM, mbody=msg, mtype="groupchat")
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
                for room_occ in self.occupants.values():
                    for n, info in room_occ.items():
                        if n.lower() == ban_nick and info.get("jid"):
                            ban_jid = info["jid"]
                            break
                    if ban_jid:
                        break

            # --- Find Nick if only JID provided ---
            ban_jid_bare = self.bare_jid(ban_jid) if ban_jid else None
            if ban_jid and not ban_nick:
                for room_occ in self.occupants.values():
                    for n, info in room_occ.items():
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
            elif not existing_is_permanent and new_is_permanent:
                # Tempban → Permanent (CONVERT)
                log.info("🔄 Converting tempban to permanent ban for %s", identifier)
                self.send_message(
                    mto=ADMIN_ROOM,
                    mbody=f"🔄 Converting tempban to permanent ban for {identifier}",
                    mtype="groupchat"
                )
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

        # --- Prevent banning admins/owners ---
        for room_occ in self.occupants.values():
            for n, info in room_occ.items():
                info_jid_bare = self.bare_jid(info.get("jid"))
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
                (ban_jid, ban_nick, ts, issuer, comment)
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

        self.ban_cache[ban_jid or ban_nick] = (
            ban_jid,
            ban_nick.lower() if ban_nick else None,
            ts,
            issuer,
            comment
        )

        log.info("Ban applied: identifier=%s, JID/Nick=%s/%s, until=%s, issuer=%s",
                 identifier, ban_jid, ban_nick, ts, issuer)

        # --- Notify Admin Room explicitly ---
        display = ban_jid or ban_nick or "Unknown"
        msg_admin = f"✅ Banned {display}" + (f" ({comment})" if comment else "") + f" by {issuer}"
        self.send_message(mto=ADMIN_ROOM, mbody=msg_admin, mtype="groupchat")

        # --- Apply ban to all protected rooms ---
        for room in self.protected_rooms:
            try:
                if is_domain:
                    # Kick all current occupants from this domain
                    for n, info in list(self.occupants.get(room, {}).items()):
                        jid_in_room = info.get("jid")
                        if jid_in_room and self.bare_jid(jid_in_room).split("@")[1].lower() == ban_jid[2:]:
                            await self.apply_ban_to_room(room, jid_in_room, n, comment, issuer)
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
                # --- Fetch expired bans ---
                async with self.db.execute(
                    "SELECT jid, nick FROM bans WHERE until > 0 AND until <= ?", (now,)
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

            except Exception as e:
                log.warning("Error in unban_worker: %s", e)
                # Don't crash, continue running
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

        # Update in-memory cache
        for key, ban in list(self.ban_cache.items()):
            jid_val, nick_val, *_ = ban
            if is_domain_ban and jid_val and jid_val.split("@")[-1].lower() == domain:
                self.ban_cache.pop(key, None)
            elif identifier == jid_val or identifier == nick_val:
                self.ban_cache.pop(key, None)

        # Unban in all protected rooms
        for room in self.protected_rooms:
            try:
                await self.apply_unban_to_room(room, ban_jid if not is_domain_ban else None, ban_nick, domain=domain if is_domain_ban else None)
            except Exception as e:
                log.warning("Error unbanning %s in %s: %s", identifier, room, e)

        # Admin Room notification
        msg_admin = f"♻️ Unbanned {identifier}" + (f" by {issuer}" if issuer else " (tempban expired)")
        self.send_message(mto=ADMIN_ROOM, mbody=msg_admin, mtype="groupchat")
        log.info(msg_admin)

    # ========== ROOM JID VALIDATION ==========

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

    # ---------- ROOM MANAGEMENT ----------

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
            rooms = "\n".join(self.protected_rooms) if self.protected_rooms else "No protected rooms."
            self.send_message(mto=room, mbody=rooms, mtype="groupchat")

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

                    asyncio.create_task(wait_for_bot_online())
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

        # --- Run all rooms in parallel ---
        await asyncio.gather(*(sync_single_room(idx + 1, room) for idx, room in enumerate(self.protected_rooms)))

        log.info("✅ Full !sync completed for %d rooms", total_rooms)
        self.send_message(
            mto=ADMIN_ROOM,
            mbody=f"✅ Full !sync completed for {total_rooms} rooms",
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

                admin_list.append(f"{nick or bare} ({bare})")

            log.info("Admins synced: %s", admin_list)

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
                    [(jid, nick, "sync_room_add", comment) for jid, nick, comment in orphan_bans]
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
        announce = getattr(self, "announce_startup", True)
        await self.sync_bans_to_rooms(startup=True, announce_progress=announce)

    async def sync_bans(self) -> None:
        await self.sync_bans_to_rooms(startup=False, announce_progress=True)


    # ---------- BANSEARCH ----------
    async def cmd_bansearch(self, query: str) -> None:
        """
        Searches bans by nick, JID, or domain.
        Supports domain bans (*.domain.tld)
        Returns detailed info including remaining time and comment.
        """
        q = query.lower()
        async with self.db.execute(
            "SELECT jid, nick, until, issuer, comment FROM bans"
        ) as cursor:
            rows = await cursor.fetchall()

        matches = []
        now = int(time.time())

        for jid, nick, until, issuer, comment in rows:
            # Domain-ban check
            if jid and jid.startswith("*."):
                display = jid
                domain = jid[2:].lower()
                haystack = domain
            else:
                display = jid or nick or "Unknown"
                haystack = " ".join(filter(None, [jid, nick])).lower()

            if q in haystack:
                remaining = human_time(max(0, until - now)) if until > 0 else "permanent"
                emoji = "⏳" if until > 0 else "🔒"
                matches.append(
                    f"{emoji} {display} ({remaining}, by {issuer}" +
                    (f", {comment}" if comment else "") + ")"
                )

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
            text = "No active bans."
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

            text = "\n".join(entries) if entries else "No active temporary bans."

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
    xmpp = BanBot(JID, PASSWORD)

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
