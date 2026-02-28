import asyncio
import logging
import time
import aiosqlite
import importlib
import config
from slixmpp import ClientXMPP
from slixmpp.exceptions import IqError, IqTimeout
from slixmpp.xmlstream import ET

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

# ---------- BAN BOT ----------
class BanBot(ClientXMPP):
    def __init__(self, jid: str, password: str):
        """
        Initialize BanBot.
        Sets up DB, protected rooms, occupants dicts, and registers XMPP plugins.
        """
        super().__init__(jid, password)
        self.db: aiosqlite.Connection | None = None

        # --- Concurrency limit for MUC write operations ---
        # Prevents flooding the XMPP server with too many IQ stanzas at once
        self.muc_write_semaphore = asyncio.Semaphore(5)

        self.protected_rooms: set[str] = set()
        self.occupants: dict[str, dict[str, dict]] = {}
        self.jid_to_nick: dict[str, str] = {}
        self.show_ban_in_muc: bool = getattr(config, "SHOW_BAN_IN_MUC", True)
        self.allow_user_cmds: bool = getattr(config, "ALLOW_USER_COMMANDS_IN_PROTECTED_ROOMS", True)

        # --- Register XMPP plugins ---
        self.register_plugin("xep_0030")  # Service Discovery
        self.register_plugin("xep_0045")  # Multi-User Chat

        # --- Event handlers ---
        self.add_event_handler("session_start", self.start)
        self.add_event_handler("groupchat_message", self.on_message)

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
    def send_ephemeral(self, mto: str, mbody: str):
        """Send a message to a room without storing it."""
        msg = self.Message()
        msg["to"] = mto
        msg["type"] = "groupchat"
        msg["body"] = mbody
        no_store = ET.Element("{urn:xmpp:hints}no-store")
        msg.append(no_store)
        msg.send()

    # ---------- COMMAND HELPERS ----------
    def notify_protected(self, room: str, message: str):
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
    async def setup_db(self):
        """Initialize SQLite DB, create tables if missing, migrate columns."""
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

        # --- Load protected rooms ---
        async with self.db.execute("SELECT room FROM rooms") as cursor:
            rows = await cursor.fetchall()
            for (room,) in rows:
                self.protected_rooms.add(room)

    # ---------- WAIT FOR OCCUPANTS ----------
    async def wait_for_occupants(self, timeout=20):
        """
        Wait until all protected rooms and admin room have at least one occupant loaded.
        Fallback to timeout if rooms are empty. Helps avoid race conditions at startup.
        """
        start = time.time()
        while time.time() - start < timeout:
            ready = True
            for r in self.protected_rooms | {ADMIN_ROOM}:
                occ = self.occupants.get(r)
                if occ is None:
                    ready = False
                    break
            if ready:
                return
            await asyncio.sleep(2)
        log.warning("Timeout waiting for occupants; some users may not be kicked immediately")

    # ---------- SESSION START ----------
    async def start(self, _):
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
        self.send_presence()
        await self.get_roster()

        # --- Join admin room ---
        self.plugin["xep_0045"].join_muc(ADMIN_ROOM, NICK)
        self.add_event_handler(f"muc::{ADMIN_ROOM}::got_online", self.muc_online)
        self.add_event_handler(f"muc::{ADMIN_ROOM}::got_offline", self.muc_offline)

        # --- Join protected rooms ---
        for room in self.protected_rooms:
            self.plugin["xep_0045"].join_muc(room, NICK)
            self.add_event_handler(f"muc::{room}::got_online", self.muc_online)
            self.add_event_handler(f"muc::{room}::got_offline", self.muc_offline)

        # --- Wait for occupants to populate ---
        await self.wait_for_occupants(timeout=20)

        # --- Sync Admins ---
        await self.sync_admins(announce=False)

        # --- Apply all bans in parallel at startup ---
        await self.sync_bans_startup()

        # --- Start unban worker ---
        asyncio.create_task(self.unban_worker())

        log.info("✅ Bot started, all rooms joined and bans applied")

    # ---------- MUC EVENT HANDLERS ----------
    async def muc_online(self, presence):
        """
        Called when a user comes online in a MUC.
        - Updates occupants
        - Skips admins/owners
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

        # --- Fetch all bans ---
        now = int(time.time())
        async with self.db.execute("SELECT jid, nick, until, comment FROM bans") as cursor:
            bans = await cursor.fetchall()

        # --- Prepare tasks for relevant bans ---
        tasks = []
        for ban_jid, ban_nick, until, comment in bans:
            # Skip expired temporary bans
            if until > 0 and until <= now:
                continue

            match_jid = ban_jid and jid_str and self.bare_jid(jid_str) == self.bare_jid(ban_jid)
            match_nick = ban_nick and nick.lower() == ban_nick.lower()
            if match_jid or match_nick:
                tasks.append(self.apply_ban_to_room(room, ban_jid, ban_nick, comment))

        # --- Run all bans in parallel ---
        if tasks:
            await asyncio.gather(*tasks)

    # ---------- MUC OFFLINE ----------
    async def muc_offline(self, presence):
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
            log.info("⛔ %s went offline in %s (jid=%s, affiliation=%s, role=%s)",
                     nick,
                     room,
                     info.get("jid", "unknown"),
                     info.get("affiliation", "none"),
                     info.get("role", "none"))

    # ---------- MESSAGE HANDLER ----------
    async def on_message(self, msg):
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
                    "!ban <jid|nick> [comment] - ban user from protected rooms\n"
                    "!tempban <jid|nick> <10m|2h|1d> [comment] - temporary ban\n"
                    "!unban <jid|nick> - remove ban\n"
                    "!bansearch <query> - search bans by nick, domain or jid\n"
                    "!banlist - show current bans\n"
                    "!room add/remove/list - manage protected rooms\n"
                    "!sync - rejoin rooms and enforce bans\n"
                    "!syncadmins - update admin list\n"
                    "!syncbans - sync bans from rooms\n"
                    "!reloadconfig - reload config.py at runtime\n"
                    "!status - bot status\n"
                    "!whoami - your affiliation\n"
                    "!why <nick|jid> - show ban reason"
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
            "!syncadmins", "!syncbans", "!status", "!whoami", "!reloadconfig"
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
                await self.sync_rooms()

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
                admin_infos = self.occupants.get(ADMIN_ROOM, {})
                admins = [
                    f"{nick} ({info['jid']})"
                    for nick, info in admin_infos.items()
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

            elif cmd == "!whoami":
                info = self.occupants.get(room, {}).get(nick, {})
                self.send_message(mto=room, mbody=f"You are {info.get('affiliation', 'none')}", mtype="groupchat")

    # ---------- HELPER ----------
    @staticmethod
    def bare_jid(jid: str) -> str | None:
        """
        Return the bare JID (without resource).
        Example: 'user@server/resource' -> 'user@server'
        """
        return jid.split("/")[0].lower() if jid else None

    # ---------- APPLY BAN TO ROOM ----------
    async def apply_ban_to_room(self, room: str, ban_jid: str | None, ban_nick: str | None, comment: str | None, issuer: str | None = None):
        """
        Apply a ban to a room:
        - Sets outcast (works offline)
        - Kicks known occupants in parallel using semaphore
        - Sends notifications according to room type
        """
        ban_jid_bare = self.bare_jid(ban_jid) if ban_jid else None
        room_occupants = self.occupants.get(room, {})

        # --- Step 1: Set Outcast (offline ban) ---
        if ban_jid_bare:
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
        async def kick_nick(nick, info):
            jid_in_room = info.get("jid")
            if ((ban_jid_bare and jid_in_room and self.bare_jid(jid_in_room) == ban_jid_bare) or
                (ban_nick and nick.lower() == ban_nick.lower())):

                # Skip admins/owners
                if info.get("affiliation") in ("owner", "admin"):
                    log.info("❌ Skipped kick for admin/owner %s in %s", nick, room)
                    return

                for attempt in range(3):
                    try:
                        async with self.muc_write_semaphore:
                            await self.plugin["xep_0045"].set_role(
                                room=room,
                                nick=nick,
                                role="none",
                                reason=comment or "Banned by admin"
                            )
                        log.info("✅ Kicked %s from %s", nick, room)
                        break
                    except IqTimeout:
                        log.warning("Timeout kicking %s in %s, retrying...", nick, room)
                        await asyncio.sleep(1)
                    except IqError as e:
                        log.warning("IqError kicking %s in %s: %s", nick, room, e)
                        break

        # Schedule all kicks concurrently
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
            except Exception:
                pass

        # --- Step 4: Notifications ---
        display = ban_jid_bare or ban_nick or "Unknown"

        if room == ADMIN_ROOM:
            msg = f"✅ Banned {display}" + (f" ({comment})" if comment else "") + f" by {issuer}"
            self.send_message(mto=ADMIN_ROOM, mbody=msg, mtype="groupchat")
        elif room in self.protected_rooms and self.allow_user_cmds:
            msg = f"✅ Banned {display}" + (f" ({comment})" if comment else "")
            self.notify_protected(room, msg)

    # ---------- BAN ALL ----------
    async def ban_all(self, identifier: str, until: int | None, issuer: str, comment: str | None = None):
        """
        Bans a user by JID or Nick:
        - Resolves JID from current room occupants if only Nick is given
        - Resolves Nick from current room occupants if only JID is given
        - Saves both JID and Nick in DB
        - Applies ban to all protected rooms
        """
        is_jid = "@" in identifier
        ban_jid = identifier if is_jid else None
        ban_nick = None if is_jid else identifier.lower()
        ts = until if until is not None else 0

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

        # --- Prevent banning admins/owners ---
        for room_occ in self.occupants.values():
            for n, info in room_occ.items():
                info_jid_bare = self.bare_jid(info.get("jid"))
                if ((ban_jid_bare and info_jid_bare == ban_jid_bare) or (ban_nick and n.lower() == ban_nick)):
                    if info.get("affiliation") in ("owner", "admin"):
                        self.send_message(
                            mto=ADMIN_ROOM,
                            mbody=f"❌ Refused to ban admin/owner: {n}",
                            mtype="groupchat"
                        )
                        return

        # --- Save ban to DB ---
        await self.db.execute(
            "REPLACE INTO bans (jid, nick, until, issuer, comment) VALUES (?, ?, ?, ?, ?)",
            (ban_jid_bare, ban_nick, ts, issuer, comment)
        )
        await self.db.commit()

        log.info("Ban applied: identifier=%s, JID=%s, nick=%s, until=%s, issuer=%s",
                 identifier, ban_jid_bare, ban_nick, ts, issuer)

        # --- Notify Admin Room explicitly ---
        display = ban_jid_bare or ban_nick or "Unknown"
        msg_admin = f"✅ Banned {display}" + (f" ({comment})" if comment else "") + f" by {issuer}"
        self.send_message(mto=ADMIN_ROOM, mbody=msg_admin, mtype="groupchat")

        # --- Apply ban to all protected rooms ---
        for room in self.protected_rooms:
            try:
                await self.apply_ban_to_room(room, ban_jid_bare, ban_nick, comment, issuer)
            except (IqError, IqTimeout) as e:
                log.warning("Failed to ban/kick %s in %s: %s", identifier, room, e)

    # ---------- UNBAN WORKER ----------
    async def unban_worker(self):
        """
        Periodically unban users whose temporary bans have expired.
        Runs in an infinite loop every 60 seconds.
        """
        while True:
            now = int(time.time())
            try:
                # --- Fetch expired bans ---
                async with self.db.execute(
                    "SELECT jid, nick FROM bans WHERE until > 0 AND until <= ?", (now,)
                ) as cursor:
                    rows = await cursor.fetchall()

                for ban_jid, ban_nick in rows:
                    identifier = self.bare_jid(ban_jid) if ban_jid else ban_nick
                    log.info("⏳ Temporary ban expired: %s, auto-unbanning...", identifier)
                    await self.unban_all(identifier, issuer="system")

            except Exception as e:
                log.warning("Error in unban_worker: %s", e)

            await asyncio.sleep(60)

    # ---------- APPLY UNBAN TO ROOM ----------
    async def apply_unban_to_room(
        self,
        room: str,
        ban_jid: str | None,
        ban_nick: str | None
    ):
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

            # --- Step 2: Restore role if online ---
            room_occupants = self.occupants.get(room, {})
            for nick, info in room_occupants.items():
                jid_in_room = info.get("jid")
                if ((ban_jid and jid_in_room and self.bare_jid(jid_in_room) == self.bare_jid(ban_jid)) or
                    (ban_nick and nick.lower() == ban_nick)):
                    for attempt in range(2):
                        try:
                            async with self.muc_write_semaphore:
                                await self.plugin["xep_0045"].set_role(
                                    room=room,
                                    nick=nick,
                                    role="participant"
                                )
                            log.info("✅ Participant role restored for %s in %s", nick, room)
                            break
                        except IqTimeout:
                            log.warning("Timeout restoring role for %s in %s, retrying...", nick, room)
                            await asyncio.sleep(1)

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
    async def unban_all(self, identifier: str, issuer: str | None = None):
        """
        Remove a ban from a user (JID or nick) and unban in all protected rooms.
        Admin Room: full info
        Protected Rooms: only nick, JID anonymized
        """
        if not identifier:
            return

        row = None
        is_jid = "@" in identifier

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
        #await self.db.execute("DELETE FROM bans WHERE jid=? OR LOWER(nick)=?", (ban_jid, ban_nick))
        #await self.db.execute("DELETE FROM bans WHERE (jid=? OR ? IS NULL) OR LOWER(nick)=?", (ban_jid, ban_jid, ban_nick))
        if ban_jid:
            await self.db.execute("DELETE FROM bans WHERE jid=? OR LOWER(nick)=?", (ban_jid, ban_nick))
        else:
            await self.db.execute("DELETE FROM bans WHERE LOWER(nick)=?", (ban_nick,))
        await self.db.commit()

        # Unban in all protected rooms
        for room in self.protected_rooms:
            try:
                await self.apply_unban_to_room(room, ban_jid, ban_nick)
            except Exception as e:
                log.warning("Error unbanning %s in %s: %s", identifier, room, e)

        # Admin Room notification
        msg_admin = f"♻️ Unbanned {identifier}" + (f" by {issuer}" if issuer else " (tempban expired)")
        self.send_message(mto=ADMIN_ROOM, mbody=msg_admin, mtype="groupchat")
        log.info(msg_admin)

    # ---------- BANSEARCH ----------
    async def cmd_bansearch(self, query: str):
        """
        Searches bans by nick, JID, or domain.
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
            haystack = " ".join(filter(None, [jid, nick])).lower()
            if q in haystack:
                remaining = human_time(max(0, until - now)) if until > 0 else "permanent"
                emoji = "⏳" if until > 0 else "🔒"
                display = jid or nick or "Unknown"

                matches.append(
                    f"{emoji} {display} ({remaining}, by {issuer}"
                    + (f", {comment}" if comment else "")
                    + ")"
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
    async def cmd_banlist(self, room):
        """
        Shows active bans.
        Admin Room: full info (JID/nick)
        Protected Rooms: only temporary bans, nick only
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

                if room == ADMIN_ROOM:
                    display = jid or nick or "Unknown"
                else:
                    display = nick or (jid.split("@")[0] if jid else "Unknown")

                entry = f"{emoji} {display} ({remaining}, by {issuer}" + (f", {comment}" if comment else "") + ")"
                entries.append(entry)

            text = "\n".join(entries) if entries else "No active temporary bans."

        if room != ADMIN_ROOM:
            self.notify_protected(room, text)
        else:
            self.send_message(mto=room, mbody=text, mtype="groupchat")

    # ---------- WHY ----------
    async def cmd_why(self, identifier, room):
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
            self.notify_protected(room, msg)
        else:
            self.send_message(mto=room, mbody=msg, mtype="groupchat")

    # ---------- ROOM MANAGEMENT ----------

    async def sync_bans_to_rooms_for_single_room(self, room: str):
        """
        Sync bans for a single room, immediately after !room add.
        Also detects outcasts already in the room but not present in the DB.
        """
        try:
            now = int(time.time())
            issuer_tag = "sync_room_add"

            # --- Load all bans from DB ---
            async with self.db.execute("SELECT jid, nick, until, comment FROM bans") as cursor:
                db_bans = await cursor.fetchall()

            # --- Fetch current Outcasts in the area ---
            outcasts = await self.plugin["xep_0045"].get_users_by_affiliation(room, "outcast")
            outcasts_bare = [self.bare_jid(str(j)) for j in outcasts]

            # --- Add orphan outcasts to database ---
            to_insert = []
            for jid_bare in outcasts_bare:
                if not any(ban_jid and self.bare_jid(ban_jid) == jid_bare for ban_jid, _, _, _ in db_bans):
                    to_insert.append((jid_bare, None, 0, issuer_tag, "Recovered from room"))
                    db_bans.append((jid_bare, None, 0, issuer_tag))  # Keep local list synchronized

            if to_insert:
                await self.db.executemany(
                    "INSERT INTO bans (jid, nick, until, issuer, comment) VALUES (?, ?, ?, ?, ?)",
                    to_insert
                )
                await self.db.commit()
                log.info("✅ Added %d orphan outcasts to DB for room %s", len(to_insert), room)

            # --- Apply all bans in this room ---
            tasks = []
            for ban_jid, ban_nick, until, comment in db_bans:
                # Skip temporary expired bans
                if until > 0 and until <= now:
                    continue
                tasks.append(self.apply_ban_to_room(room, ban_jid, ban_nick, comment))

            if tasks:
                await asyncio.gather(*tasks)

            log.info("✅ Initial ban sync completed for newly added room: %s", room)

        except Exception as e:
            log.warning("⚠️ Failed to sync bans for room %s: %s", room, e)

    async def cmd_room(self, args, room):
        """
        Manage protected rooms.
        Commands: list, add <room>, remove <room>
        """
        if not args:
            return

        action = args[0].lower()
        if action == "list":
            rooms = "\n".join(self.protected_rooms) if self.protected_rooms else "No protected rooms."
            self.send_message(mto=room, mbody=rooms, mtype="groupchat")

        elif action in ("add", "remove") and len(args) >= 2:
            target = args[1]

            if action == "add":
                if target not in self.protected_rooms:
                    # --- In-Memory and DB ---
                    self.protected_rooms.add(target)
                    await self.db.execute("INSERT OR REPLACE INTO rooms (room) VALUES (?)", (target,))
                    await self.db.commit()
                    self.send_message(mto=room, mbody=f"✅ Room added: {target}", mtype="groupchat")

                    # --- Event handler for new occupants ---
                    self.add_event_handler(f"muc::{target}::got_online", self.muc_online)
                    self.add_event_handler(f"muc::{target}::got_offline", self.muc_offline)

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
    async def sync_rooms(self):
        """
        Rejoin all protected rooms.
        Useful if bot was disconnected or to re-sync occupants.
        """
        for room in self.protected_rooms:
            self.plugin["xep_0045"].join_muc(room, NICK)

        log.info("Rooms synced.")

        self.send_message(
            mto=ADMIN_ROOM,
            mbody="🔄 Rooms synced successfully.",
            mtype="groupchat"
        )

    async def sync_admins(self, announce: bool = False):
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

    # ---------- BAN SYNC ----------
    async def sync_bans_to_rooms(self, startup: bool = False, announce_progress: bool = True):
        """
        Generic ban sync to protected rooms.
        - startup=True: skips expired temporary bans, issuer="sync_startup"
        - startup=False: sync called manually, issuer="sync"
        """
        now = int(time.time())
        issuer_tag = "sync_startup" if startup else "sync"

        # --- Load all bans from DB ---
        async with self.db.execute("SELECT jid, nick, until, comment FROM bans") as cursor:
            db_bans = await cursor.fetchall()

        total_rooms = len(self.protected_rooms)
        if total_rooms == 0:
            if announce_progress:
                self.send_message(
                    mto=ADMIN_ROOM,
                    mbody="⚠️ No protected rooms to sync.",
                    mtype="groupchat"
                )
            return

        applied_bans = set()  # only used for startup to skip duplicates
        startup_applied_count = 0  # count of applied bans during startup

        async def sync_room(idx: int, room: str):
            nonlocal startup_applied_count

            if announce_progress and not startup:
                self.send_message(
                    mto=ADMIN_ROOM,
                    mbody=f"⏳ Syncing bans in room {room} ({idx}/{total_rooms})...",
                    mtype="groupchat"
                )

            try:
                # --- Fetch current outcasts ---
                outcasts = await self.plugin["xep_0045"].get_users_by_affiliation(room, "outcast")
                outcasts_bare = [self.bare_jid(str(j)) for j in outcasts]

                # --- Ensure DB entries exist for orphan outcasts ---
                to_insert = []
                for jid_bare in outcasts_bare:
                    if not any(ban_jid and self.bare_jid(ban_jid) == jid_bare for ban_jid, _, _, _ in db_bans):
                        to_insert.append((jid_bare, None, 0, issuer_tag, "Recovered from room"))
                        db_bans.append((jid_bare, None, 0, issuer_tag))  # keep local list in sync

                if to_insert:
                    await self.db.executemany(
                        "INSERT INTO bans (jid, nick, until, issuer, comment) VALUES (?, ?, ?, ?, ?)",
                        to_insert
                    )
                    await self.db.commit()

                # --- Fetch current outcasts ---
                outcasts = await self.plugin["xep_0045"].get_users_by_affiliation(room, "outcast")
                outcasts_bare = {self.bare_jid(str(j)) for j in outcasts}

                # --- Prepare coroutines for all bans ---
                tasks = []
                for ban_jid, ban_nick, until, comment in db_bans:
                    if until > 0 and until <= now:  # Skip expired temporary bans
                        continue

                    ban_jid_bare = self.bare_jid(ban_jid) if ban_jid else None
                    # Skip if already an outcast in this room
                    if ban_jid_bare and ban_jid_bare in outcasts_bare:
                        continue

                    tasks.append(self.apply_ban_to_room(room, ban_jid, ban_nick, comment))

                # --- Run all bans in this room in parallel ---
                if tasks:
                    await asyncio.gather(*tasks)

            except (IqError, IqTimeout) as e:
                log.warning("Failed to sync bans in %s: %s", room, e)
            #else:
            #    if announce_progress:
            #        self.send_message(
            #            mto=ADMIN_ROOM,
            #            mbody=f"✅ Finished syncing bans in room {room} ({idx}/{total_rooms})",
            #            mtype="groupchat"
            #        )

        # --- Run all rooms in parallel ---
        await asyncio.gather(*(sync_room(idx + 1, room) for idx, room in enumerate(self.protected_rooms)))

        # --- Final logs ---
        if startup:
            log.info("✅ Startup ban sync completed: %d bans applied in %d rooms", startup_applied_count, total_rooms)
            if announce_progress:
                self.send_message(
                    mto=ADMIN_ROOM,
                    mbody=f"✅ Startup completed: {startup_applied_count} bans applied across {total_rooms} rooms.",
                    mtype="groupchat"
                )
        else:
            log.info("✅ Ban sync completed for all rooms")
            if announce_progress:
                self.send_message(
                    mto=ADMIN_ROOM,
                    mbody=f"✅ Ban sync completed successfully for {total_rooms} rooms.",
                    mtype="groupchat"
                )

    async def sync_bans_startup(self):
        """
        Startup ban sync.
        Announce messages in Admin-Room only if ANNOUNCE_STARTUP=True in config.py
        """
        announce = getattr(self, "announce_startup", True)
        await self.sync_bans_to_rooms(startup=True, announce_progress=announce)

    async def sync_bans(self):
        await self.sync_bans_to_rooms(startup=False, announce_progress=True)

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
    else:
        log.error("Unable to connect to XMPP server.")
