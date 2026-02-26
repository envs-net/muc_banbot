import asyncio
import logging
import time
import aiosqlite
from slixmpp import ClientXMPP
from slixmpp.exceptions import IqError, IqTimeout
from slixmpp.xmlstream import ET

# ================= CONFIG =================
JID = "adminbot@domain.tld"
PASSWORD = "yourpassword"
ADMIN_ROOM = "admin@muc.domain.tld"
NICK = "adminbot"
DB_FILE = "bans.db"
# ==========================================

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

# ---------- TIME HELPERS ----------
def parse_duration(s: str) -> int:
    unit = s[-1].lower()
    value = int(s[:-1])
    return value * {"m": 60, "h": 3600, "d": 86400}[unit]

def human_time(seconds: int) -> str:
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
        super().__init__(jid, password)
        self.db = None
        self.protected_rooms = set()
        self.occupants = {}
        self.jid_to_nick = {}

        self.register_plugin("xep_0030")  # Service Discovery
        self.register_plugin("xep_0045")  # Multi-User Chat

        self.add_event_handler("session_start", self.start)
        self.add_event_handler("groupchat_message", self.on_message)

    # ---------- ADMIN / OWNER PROTECTION ----------
    def is_admin_or_owner(self, room, nick=None, jid=None) -> bool:
        occ = self.occupants.get(room, {})
        for n, info in occ.items():
            if nick and n.lower() == nick.lower():
                return info.get("affiliation") in ("owner", "admin")
            if jid and info.get("jid") and self.bare_jid(info["jid"]) == self.bare_jid(jid):
                return info.get("affiliation") in ("owner", "admin")
        return False

    # ---------- EPHEMERAL ----------
    def send_ephemeral(self, mto, mbody):
        msg = self.Message()
        msg["to"] = mto
        msg["type"] = "groupchat"
        msg["body"] = mbody
        no_store = ET.Element("{urn:xmpp:hints}no-store")
        msg.append(no_store)
        msg.send()

    # ---------- DATABASE ----------
    async def setup_db(self):
        self.db = await aiosqlite.connect(DB_FILE)

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

        await self.db.execute("""
        CREATE TABLE IF NOT EXISTS rooms (
            room TEXT PRIMARY KEY
        )""")
        await self.db.commit()

        async with self.db.execute("SELECT room FROM rooms") as cursor:
            rows = await cursor.fetchall()
            for (room,) in rows:
                self.protected_rooms.add(room)

    # ---------- SESSION START ----------
    async def start(self, _):
        await self.setup_db()
        self.send_presence()
        await self.get_roster()

        # Join admin room
        self.plugin["xep_0045"].join_muc(ADMIN_ROOM, NICK)
        self.add_event_handler(f"muc::{ADMIN_ROOM}::got_online", self.muc_online)
        self.add_event_handler(f"muc::{ADMIN_ROOM}::got_offline", self.muc_offline)
        log.info("Joined admin room %s", ADMIN_ROOM)

        # Join protected rooms
        for room in self.protected_rooms:
            self.plugin["xep_0045"].join_muc(room, NICK)
            self.add_event_handler(f"muc::{room}::got_online", self.muc_online)
            self.add_event_handler(f"muc::{room}::got_offline", self.muc_offline)
            log.info("Joined protected room %s", room)

        # --- Sync Admins immediately after joining admin room ---
        await self.sync_admins()

        # --- Startup ban sync only applies active bans ---
        await self.sync_bans_startup()

        # Background tasks
        asyncio.create_task(self.unban_worker())

    # ---------- MUC ----------
    async def muc_online(self, presence):
        room = presence["from"].bare
        nick = presence["muc"]["nick"]
        jid = presence["muc"].get("jid")
        jid_str = str(jid) if jid else None

        self.occupants.setdefault(room, {})[nick] = {
            "role": presence["muc"]["role"],
            "affiliation": presence["muc"]["affiliation"],
            "jid": jid_str,
        }

        if room == ADMIN_ROOM:
            log.info("Admin-Room occupant online: %s, role=%s, affiliation=%s",
                     nick, presence["muc"]["role"], presence["muc"]["affiliation"])

        if self.is_admin_or_owner(room, nick=nick, jid=jid_str):
            return

        now = int(time.time())
        async with self.db.execute("SELECT jid, nick, until, comment FROM bans") as cursor:
            rows = await cursor.fetchall()

        for ban_jid, ban_nick, until, comment in rows:
            if ((ban_jid and jid_str and self.bare_jid(jid_str) == self.bare_jid(ban_jid)) or
                (ban_nick and nick.lower() == ban_nick.lower())):
                if until == 0 or until > now:
                    try:
                        if ban_jid:
                            await self.plugin["xep_0045"].set_affiliation(
                                room=room,
                                jid=ban_jid,
                                affiliation="outcast",
                                reason=comment or "Banned by admin"
                            )
                        await self.plugin["xep_0045"].set_role(
                            room=room,
                            nick=nick,
                            role="none",
                            reason=comment or "Banned by admin"
                        )
                    except (IqError, IqTimeout):
                        log.warning("Failed to kick %s in %s", nick, room)

    async def muc_offline(self, presence):
        room = presence["from"].bare
        nick = presence["muc"]["nick"]
        self.occupants.get(room, {}).pop(nick, None)

    # ---------- AUTH ----------
    def is_authorized(self, msg) -> bool:
        if msg["from"].bare != ADMIN_ROOM:
            return False
        info = self.occupants.get(ADMIN_ROOM, {}).get(msg["mucnick"])
        return info and info.get("affiliation") in ("owner", "admin")

    # ---------- MESSAGE ----------
    async def on_message(self, msg):
        if msg["mucnick"] == NICK:
            return
        room = msg["from"].bare
        nick = msg["mucnick"]
        body = msg["body"].strip()
        parts = body.split()
        cmd = parts[0] if parts else ""

        # ---------- HELP ----------
        if cmd == "!help":
            if room == ADMIN_ROOM and self.is_authorized(msg):
                self.send_message(
                    mto=room,
                    mbody=(
                        "!help - show this help\n"
                        "!ban <jid|nick> [comment] - ban user from protected rooms\n"
                        "!tempban <jid|nick> <10m|2h|1d> [comment] - temporary ban\n"
                        "!unban <jid|nick> - remove ban\n"
                        "!banlist - show current bans\n"
                        "!room add/remove/list - manage protected rooms\n"
                        "!sync - rejoin rooms and enforce bans\n"
                        "!syncadmins - update admin list\n"
                        "!syncbans - sync bans from rooms\n"
                        "!status - bot status\n"
                        "!whoami - your affiliation\n"
                        "!why <nick|jid> - show ban reason"
                    ),
                    mtype="groupchat"
                )
            elif room in self.protected_rooms:
                self.send_message(
                    mto=room,
                    mbody="!help - show this help\n!banlist - show temporary bans\n!why <nick> - show ban reason",
                    mtype="groupchat"
                )
            return

        # ---------- BANLIST ----------
        if cmd == "!banlist":
            if room == ADMIN_ROOM or room in self.protected_rooms:
                await self.cmd_banlist(room)
            else:
                self.send_message(mto=room, mbody="❌ You are not authorized.", mtype="groupchat")
            return

        # ---------- ADMIN COMMANDS ----------
        admin_commands = ("!ban", "!tempban", "!unban", "!room", "!sync", "!syncadmins", "!syncbans", "!status", "!whoami")
        if cmd in admin_commands:
            if room != ADMIN_ROOM or not self.is_authorized(msg):
                self.send_message(mto=room, mbody="❌ You are not authorized.", mtype="groupchat")
                return

            if cmd == "!ban" and len(parts) >= 2:
                comment = " ".join(parts[2:]) if len(parts) > 2 else None
                await self.ban_all(parts[1], None, nick, comment)
            elif cmd == "!tempban" and len(parts) >= 3:
                try:
                    until = int(time.time()) + parse_duration(parts[2])
                except Exception:
                    self.send_message(mto=room, mbody="❌ Invalid duration format (10m, 2h, 1d).", mtype="groupchat")
                    return
                comment = " ".join(parts[3:]) if len(parts) > 3 else None
                await self.ban_all(parts[1], until, nick, comment)
            elif cmd == "!unban" and len(parts) >= 2:
                await self.unban_all(parts[1], nick)
            elif cmd == "!room" and len(parts) >= 2:
                await self.cmd_room(parts[1:], room)
            elif cmd == "!sync":
                await self.sync_rooms()
            elif cmd == "!syncadmins":
                await self.sync_admins()
            elif cmd == "!syncbans":
                await self.sync_bans()
            elif cmd == "!status":
                self.send_message(mto=room, mbody="✅ Bot is online and healthy.", mtype="groupchat")
            elif cmd == "!whoami":
                info = self.occupants.get(room, {}).get(nick, {})
                self.send_message(mto=room, mbody=f"You are {info.get('affiliation', 'none')}", mtype="groupchat")
            return

        # ---------- WHY ----------
        if cmd == "!why" and len(parts) >= 2:
            await self.cmd_why(parts[1], room)

    # ---------- HELPER ----------
    @staticmethod
    def bare_jid(jid: str) -> str:
        return jid.split("/")[0] if jid else None

    # ---------- BAN HANDLING ----------
    async def ban_all(self, identifier: str, until: int | None, issuer: str, comment: str | None = None):
        is_jid = "@" in identifier
        ban_jid = identifier if is_jid else None
        ban_nick = None if is_jid else identifier.lower()
        ts = until if until else 0

        # Resolve JID if only nick given
        if ban_nick:
            for room_occupants in self.occupants.values():
                for n, info in room_occupants.items():
                    if n.lower() == ban_nick and info.get("jid"):
                        ban_jid = info["jid"]
                        break
                if ban_jid:
                    break

        for room_occ in self.occupants.values():
            for n, info in room_occ.items():
                if ((ban_jid and info.get("jid") and self.bare_jid(info["jid"]) == self.bare_jid(ban_jid)) or
                    (ban_nick and n.lower() == ban_nick)):
                    if info.get("affiliation") in ("owner", "admin"):
                        log.info("Refused to ban admin/owner: %s", n)
                        # Nachricht an Admin-Raum senden
                        self.send_message(
                            mto=ADMIN_ROOM,
                            mbody=f"❌ Refused to ban admin/owner: {n}",
                            mtype="groupchat"
                        )
                        return

        ban_jid_bare = self.bare_jid(ban_jid)

        # Store in DB
        await self.db.execute(
            "REPLACE INTO bans (jid, nick, until, issuer, comment) VALUES (?, ?, ?, ?, ?)",
            (ban_jid_bare, ban_nick, ts, issuer, comment)
        )
        await self.db.commit()

        # Prepare display strings
        display_admin = ban_jid_bare or ban_nick or identifier
        display_protected = ban_nick or (ban_jid_bare.split("@")[0] if ban_jid_bare else identifier)
        msg_admin = f"✅ Banned {display_admin}" + (f" ({comment})" if comment else "") + f" by {issuer}"
        msg_protected = f"✅ Banned {display_protected}" + (f" ({comment})" if comment else "") + f" by {issuer}"

        for room in self.protected_rooms:
            try:
                # Kick + Outcast
                if ban_jid_bare:
                    await self.plugin["xep_0045"].set_affiliation(
                        room=room,
                        jid=ban_jid_bare,
                        affiliation="outcast",
                        reason=comment or "Banned by admin"
                    )

                room_occupants = self.occupants.get(room, {})
                for n, info in room_occupants.items():
                    if (ban_jid_bare and self.bare_jid(info.get("jid")) == ban_jid_bare) or (ban_nick and n.lower() == ban_nick):
                        await self.plugin["xep_0045"].set_role(
                            room=room,
                            nick=n,
                            role="none",
                            reason=comment or "Banned by admin"
                        )
                        log.info("Kicked %s from %s", n, room)

                # Send messages
                if room == ADMIN_ROOM:
                    self.send_message(mto=room, mbody=msg_admin, mtype="groupchat")
                else:
                    self.send_ephemeral(room, msg_protected)
                    # Admin room always receives msg_admin
                    self.send_message(mto=ADMIN_ROOM, mbody=f"[{room}] {msg_admin}", mtype="groupchat")

            except (IqError, IqTimeout) as e:
                log.warning("Failed to ban/kick %s in %s: %s", identifier, room, e)

        log.info(f"Banned: {display_admin} (issuer: {issuer})")

    async def unban_all(self, identifier: str, issuer: str | None = None):
        if not identifier:
            return
        is_jid = "@" in identifier
        ban_jid = identifier if is_jid else None
        ban_nick = None if is_jid else identifier.lower()

        await self.db.execute("DELETE FROM bans WHERE jid = ? OR nick = ?", (ban_jid, ban_nick))
        await self.db.commit()

        # Remove affiliation in protected rooms
        for room in self.protected_rooms:
            try:
                if ban_jid:
                    await self.plugin["xep_0045"].set_affiliation(room=room, jid=ban_jid, affiliation="none")
                elif ban_nick:
                    for n, info in self.occupants.get(room, {}).items():
                        if n.lower() == ban_nick and info.get("jid"):
                            await self.plugin["xep_0045"].set_affiliation(room=room, jid=info["jid"], affiliation="none")
            except (IqError, IqTimeout) as e:
                log.warning("Failed to unban %s in %s: %s", identifier, room, e)

        # Determine display name
        display = ban_nick or None
        if not display and ban_jid:
            for room_occ in self.occupants.values():
                for n, info in room_occ.items():
                    if info.get("jid") == ban_jid:
                        display = n
                        break
                if display:
                    break
        if not display:
            display = ban_jid or "Unknown"

        msg_admin = f"♻️ Unbanned {display}" + (f" by {issuer}" if issuer and issuer != "system" else " (tempban expired)")
        msg_protected = f"♻️ Unbanned {ban_nick or (ban_jid.split('@')[0] if ban_jid else 'Unknown')}" + \
                        (f" by {issuer}" if issuer and issuer != "system" else " (tempban expired)")

        for room in self.protected_rooms:
            if room == ADMIN_ROOM:
                self.send_message(mto=room, mbody=msg_admin, mtype="groupchat")
            else:
                self.send_ephemeral(room, msg_protected)
                # Admin room always receives msg_admin
                self.send_message(mto=ADMIN_ROOM, mbody=f"[{room}] {msg_admin}", mtype="groupchat")

        log.info(msg_admin)

    # ---------- BANLIST ----------
    async def cmd_banlist(self, room):
        async with self.db.execute("SELECT jid, nick, until, issuer, comment FROM bans") as cursor:
            rows = await cursor.fetchall()

        if not rows:
            text = "No active bans."
        else:
            now = int(time.time())
            entries = []
            for jid, nick, until, issuer, comment in rows:
                if room != ADMIN_ROOM and until <= 0:
                    continue  # skip permanent in protected rooms
                remaining = human_time(until - now) if until > 0 else "permanent"
                emoji = "⏳" if until > 0 else "🔒"

                if room == ADMIN_ROOM:
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
    async def cmd_why(self, identifier, room):
        is_jid = "@" in identifier
        ban_jid = identifier if is_jid else None
        ban_nick = None if is_jid else identifier.lower()

        row = None
        # 1. Check direct JID
        if ban_jid:
            async with self.db.execute(
                "SELECT jid, nick, until, issuer, comment FROM bans WHERE jid=?",
                (ban_jid,)
            ) as cursor:
                row = await cursor.fetchone()

        # 2. Check Nick
        if not row:
            async with self.db.execute(
                "SELECT jid, nick, until, issuer, comment FROM bans WHERE LOWER(nick)=?",
                (ban_nick,)
            ) as cursor:
                row = await cursor.fetchone()

        # 3. Check Nick against JIDs in DB
        if not row and ban_nick:
            async with self.db.execute("SELECT jid, nick, until, issuer, comment FROM bans") as cursor:
                async for jid_db, nick_db, until, issuer, comment in cursor:
                    if jid_db and self.bare_jid(jid_db).split("@")[0].lower() == ban_nick:
                        row = (jid_db, nick_db, until, issuer, comment)
                        break

        if row:
            jid_db, nick_db, until, issuer, comment = row
            now = int(time.time())
            remaining = human_time(until - now) if until > 0 else "permanent"
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

    # ---------- ROOM MANAGEMENT ----------
    async def cmd_room(self, args, room):
        if not args:
            return
        action = args[0].lower()
        if action == "list":
            rooms = "\n".join(self.protected_rooms) if self.protected_rooms else "No protected rooms."
            self.send_message(mto=room, mbody=rooms, mtype="groupchat")
        elif action in ("add", "remove") and len(args) >= 2:
            target = args[1]
            if action == "add":
                self.protected_rooms.add(target)
                await self.db.execute("INSERT OR REPLACE INTO rooms (room) VALUES (?)", (target,))
                await self.db.commit()
                self.send_message(mto=room, mbody=f"✅ Room added: {target}", mtype="groupchat")
            elif action == "remove":
                self.protected_rooms.discard(target)
                await self.db.execute("DELETE FROM rooms WHERE room=?", (target,))
                await self.db.commit()
                self.send_message(mto=room, mbody=f"✅ Room removed: {target}", mtype="groupchat")

    # ---------- SYNC ----------
    async def sync_rooms(self):
        for room in self.protected_rooms:
            self.plugin["xep_0045"].join_muc(room, NICK)
        log.info("Rooms synced.")

    async def sync_admins(self):
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

            if admin_list:
                msg = "✅ Current admins/owners in Admin-Room:\n" + "\n".join(admin_list)
            else:
                msg = "⚠️ No admins/owners found in Admin-Room."

            self.send_message(mto=ADMIN_ROOM, mbody=msg, mtype="groupchat")
            log.info("Admins synced: %s", admin_list)

        except Exception as e:
            log.warning("Failed to sync admins: %s", e)

    # ---------- BAN SYNC ----------
    async def sync_bans_startup(self):
        """Initial ban sync on bot startup: apply only active bans, avoid duplicates."""
        now = int(time.time())
        applied_bans = set()

        async with self.db.execute("SELECT jid, nick, until, comment FROM bans") as cursor:
            rows = await cursor.fetchall()
            for jid, nick, until, comment in rows:
                # Skip expired temporary bans
                if until > 0 and until <= now:
                    continue

                identifier = jid or nick
                key = self.bare_jid(jid) if jid else nick.lower()
                if key in applied_bans:
                    continue  # Already applied
                await self.ban_all(identifier, until, "system", comment)
                applied_bans.add(key)

        log.info("✅ Startup ban sync completed.")

    async def sync_bans(self):
        """Full sync: read outcasts from rooms and reapply all bans from DB, optimized to avoid duplicate kicks."""
        now = int(time.time())
        applied_bans = set()  # Speichert bare_jid oder nick_lower

        for room in self.protected_rooms:
            try:
                # --- Get all outcasts from room ---
                outcasts = await self.plugin["xep_0045"].get_users_by_affiliation(room, affiliation="outcast")

                for jid in outcasts:
                    jid_str = str(jid)
                    jid_bare = self.bare_jid(jid_str)
                    if jid_bare in applied_bans:
                        continue  # Schon bearbeitet
                    async with self.db.execute("SELECT jid, until FROM bans WHERE jid=?", (jid_str,)) as cursor:
                        row = await cursor.fetchone()
                    if not row:
                        # Neue permanente Ban-Einträge
                        await self.db.execute(
                            "INSERT INTO bans (jid, nick, until, issuer, comment) VALUES (?, ?, ?, ?, ?)",
                            (jid_str, None, 0, "sync", None)
                        )
                        await self.db.commit()
                        log.info("Synced new permanent ban from %s: %s", room, jid_str)

                    # Ban anwenden (Kick + Outcast) nur einmal
                    await self.ban_all(jid_str, 0, "sync")
                    applied_bans.add(jid_bare)

            except (IqError, IqTimeout) as e:
                log.error("Failed to sync bans in %s: %s", room, e)

        # Reapply all bans from DB (temporäre + permanente), nur für noch nicht behandelte
        async with self.db.execute("SELECT jid, nick, until, comment FROM bans") as cursor:
            rows = await cursor.fetchall()
            for jid, nick, until, comment in rows:
                identifier = jid or nick
                key = self.bare_jid(jid) if jid else nick.lower()
                if key in applied_bans:
                    continue  # Schon bearbeitet
                await self.ban_all(identifier, until, "system", comment)
                applied_bans.add(key)

        log.info("✅ Full ban sync completed.")

    # ---------- UNBAN WORKER ----------
    async def unban_worker(self):
        while True:
            now = int(time.time())
            async with self.db.execute("SELECT jid, nick, until FROM bans WHERE until > 0 AND until <= ?", (now,)) as cursor:
                rows = await cursor.fetchall()
            for jid, nick, until in rows:
                await self.unban_all(jid or nick, "system")
            await asyncio.sleep(10)

# ---------- RUN BOT ----------
if __name__ == "__main__":
    xmpp = BanBot(JID, PASSWORD)
    if xmpp.connect():
        log.info("Connected successfully. Starting event loop...")
        try:
            xmpp.loop.run_forever()
        except KeyboardInterrupt:
            log.info("Bot stopped manually.")
    else:
        log.error("Unable to connect to XMPP server.")
