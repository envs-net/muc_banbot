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
    unit = s[-1]
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

    # ---------- EPHEMERAL MESSAGES ----------
    def send_ephemeral(self, mto, mbody):
        msg = self.Message()
        msg['to'] = mto
        msg['type'] = 'groupchat'
        msg['body'] = mbody
        no_store = ET.Element('{urn:xmpp:hints}no-store')
        msg.append(no_store)
        msg.send()

    # ---------- DATABASE ----------
    async def setup_db(self):
        self.db = await aiosqlite.connect(DB_FILE)
        await self.db.execute("""
        CREATE TABLE IF NOT EXISTS bans (
            jid TEXT PRIMARY KEY,
            until INTEGER,
            issuer TEXT,
            comment TEXT
        )""")
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

        # Join all protected rooms
        for room in self.protected_rooms:
            self.plugin["xep_0045"].join_muc(room, NICK)
            self.add_event_handler(f"muc::{room}::got_online", self.muc_online)
            self.add_event_handler(f"muc::{room}::got_offline", self.muc_offline)
            log.info("Joined protected room %s", room)

        # Background tasks
        asyncio.create_task(self.unban_worker())
        asyncio.create_task(self.sync_bans())

    # ---------- MUC OCCUPANTS ----------
    async def muc_online(self, presence):
        room = presence['from'].bare
        nick = presence['muc']['nick']
        role = presence['muc']['role']
        affiliation = presence['muc']['affiliation']
        jid = presence['muc'].get('jid')
        self.occupants.setdefault(room, {})[nick] = {"role": role, "affiliation": affiliation, "jid": jid}
        if jid:
            self.jid_to_nick[jid] = nick
        if room == ADMIN_ROOM:
            log.info("Occupant online: %s, role=%s, affiliation=%s", nick, role, affiliation)

    async def muc_offline(self, presence):
        room = presence['from'].bare
        nick = presence['muc']['nick']
        self.occupants.get(room, {}).pop(nick, None)
        if room == ADMIN_ROOM:
            log.info("Occupant offline: %s", nick)

    # ---------- ADMIN CHECK ----------
    def is_authorized(self, msg) -> bool:
        if msg["from"].bare != ADMIN_ROOM:
            return False
        jid = self.occupants.get(ADMIN_ROOM, {}).get(msg["mucnick"], {}).get("jid")
        return jid in self.jid_to_nick and self.occupants[ADMIN_ROOM][msg["mucnick"]]["affiliation"] in ("owner", "admin")

    def is_owner(self, msg) -> bool:
        if msg["from"].bare != ADMIN_ROOM:
            return False
        jid = self.occupants.get(ADMIN_ROOM, {}).get(msg["mucnick"], {}).get("jid")
        return jid in self.jid_to_nick and self.occupants[ADMIN_ROOM][msg["mucnick"]]["affiliation"] == "owner"

    # ---------- MESSAGE HANDLER ----------
    async def on_message(self, msg):
        if msg["mucnick"] == NICK:
            return
        room = msg["from"].bare
        nick = msg["mucnick"]
        body = msg["body"].strip()

        if room == ADMIN_ROOM:
            log.info("Message from %s (room=%s): %s", nick, room, body)

        # Help
        if body == "!help" and room == ADMIN_ROOM:
            self.send_message(
                mto=room,
                mbody=(
                    "!help - show this help\n"
                    "!ban <jid|nick> [comment] - ban user from protected rooms\n"
                    "!tempban <jid|nick> <10m|2h|1d> [comment] - temporary ban\n"
                    "!unban <jid> - remove ban\n"
                    "!banlist - show current bans\n"
                    "!room add/remove/list - manage protected rooms\n"
                    "!sync - rejoin rooms and enforce bans\n"
                    "!syncadmins - update admin list\n"
                    "!syncbans - sync bans from rooms\n"
                    "!status - bot status\n"
                    "!whoami - your affiliation"
                ),
                mtype="groupchat",
            )
            return

        if room != ADMIN_ROOM:
            return

        # Authorization check
        if not self.is_authorized(msg):
            self.send_message(mto=room, mbody="❌ You are not authorized.", mtype="groupchat")
            return

        parts = body.split()
        cmd = parts[0]

        if cmd == "!ban" and len(parts) >= 2:
            comment = " ".join(parts[2:]) if len(parts) > 2 else None
            await self.ban_all(parts[1], None, nick, comment)
        elif cmd == "!tempban" and len(parts) >= 3:
            until = int(time.time()) + parse_duration(parts[2])
            comment = " ".join(parts[3:]) if len(parts) > 3 else None
            await self.ban_all(parts[1], until, nick, comment)
        elif cmd == "!unban" and len(parts) >= 2:
            await self.unban_all(parts[1], nick)
        elif cmd == "!banlist":
            await self.cmd_banlist(room)
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

    # ---------- BAN HANDLING ----------
    async def ban_all(self, jid: str, until: int | None, issuer: str, comment: str | None = None):
        nick_display = self.jid_to_nick.get(jid, jid.split('@')[0])
        for room in self.protected_rooms:
            try:
                await self.plugin["xep_0045"].set_affiliation(jid=jid, affiliation="outcast", room=room)
                ts = until if until else 0
                await self.db.execute(
                    "REPLACE INTO bans VALUES (?, ?, ?, ?)",
                    (jid, ts, issuer, comment)
                )
                await self.db.commit()
                msg = f"✅ Banned {nick_display}" + (f" ({comment})" if comment else "")
                if room != ADMIN_ROOM:
                    self.send_ephemeral(room, msg)
                    self.send_message(mto=ADMIN_ROOM, mbody=f"[{room}] {msg} by {issuer}", mtype="groupchat")
                else:
                    self.send_message(mto=room, mbody=msg, mtype="groupchat")
            except (IqError, IqTimeout) as e:
                log.error("Ban failed in %s: %s", room, e)

    async def unban_all(self, jid: str, issuer: str | None = None):
        nick_display = self.jid_to_nick.get(jid, jid.split('@')[0])
        for room in self.protected_rooms:
            try:
                await self.plugin["xep_0045"].set_affiliation(jid=jid, affiliation="none", room=room)
                await self.db.execute("DELETE FROM bans WHERE jid=?", (jid,))
                await self.db.commit()
                msg = f"♻️ Unbanned {nick_display}" + (f" by {issuer}" if issuer else "")
                if room != ADMIN_ROOM:
                    self.send_ephemeral(room, msg)
                    self.send_message(mto=ADMIN_ROOM, mbody=f"[{room}] {msg}", mtype="groupchat")
                else:
                    self.send_message(mto=room, mbody=msg, mtype="groupchat")
            except (IqError, IqTimeout) as e:
                log.error("Unban failed in %s: %s", room, e)

    async def cmd_banlist(self, room):
        async with self.db.execute("SELECT jid, until, issuer, comment FROM bans") as cursor:
            rows = await cursor.fetchall()
        if not rows:
            text = "No active bans."
        else:
            now = int(time.time())
            text = "\n".join(
                f"{jid} (remaining {human_time(until - now)}, by {issuer}" +
                (f", {comment}" if comment else "") + ")" if until > 0
                else f"{jid} (permanent, by {issuer}" +
                     (f", {comment}" if comment else "") + ")"
                for jid, until, issuer, comment in rows
            )
        self.send_message(mto=room, mbody=text, mtype="groupchat")

    # ---------- ROOM MANAGEMENT ----------
    async def cmd_room(self, args, room):
        subcmd = args[0]
        if subcmd == "add" and len(args) == 2:
            new_room = args[1]
            self.protected_rooms.add(new_room)
            await self.db.execute("INSERT OR REPLACE INTO rooms VALUES (?)", (new_room,))
            await self.db.commit()
            self.plugin["xep_0045"].join_muc(new_room, NICK)
            self.add_event_handler(f"muc::{new_room}::got_online", self.muc_online)
            self.add_event_handler(f"muc::{new_room}::got_offline", self.muc_offline)
            self.send_message(mto=room, mbody=f"✅ Added protection for {new_room}", mtype="groupchat")
        elif subcmd == "remove" and len(args) == 2:
            old_room = args[1]
            self.protected_rooms.discard(old_room)
            await self.db.execute("DELETE FROM rooms WHERE room=?", (old_room,))
            await self.db.commit()
            self.send_message(mto=room, mbody=f"♻️ Removed protection for {old_room}", mtype="groupchat")
        elif subcmd == "list":
            if not self.protected_rooms:
                self.send_message(mto=room, mbody="No protected rooms.", mtype="groupchat")
            else:
                self.send_message(mto=room, mbody="\n".join(self.protected_rooms), mtype="groupchat")

    async def sync_rooms(self):
        for room in self.protected_rooms:
            self.plugin["xep_0045"].join_muc(room, NICK)
        async with self.db.execute("SELECT jid, until, issuer, comment FROM bans") as cursor:
            rows = await cursor.fetchall()
        now = int(time.time())
        for jid, until, issuer, comment in rows:
            if until == 0 or until > now:
                await self.ban_all(jid, until, issuer, comment)

    # ---------- SYNC ADMINS ----------
    async def sync_admins(self, room=None):
        room = room or ADMIN_ROOM
        occupants_room = self.occupants.setdefault(room, {})

        for affiliation in ('owner', 'admin'):
            try:
                result = await self.plugin['xep_0045'].get_users_by_affiliation(room, affiliation)
            except (IqError, IqTimeout) as e:
                log.error("Failed to fetch %s in %s: %s", affiliation, room, e)
                continue

            for jid in result:  # result is a list of JIDs (str)
                nick = self.jid_to_nick.get(jid, jid.split('@')[0])
                occupants_room[nick] = occupants_room.get(nick, {})
                occupants_room[nick]['affiliation'] = affiliation
                occupants_room[nick]['jid'] = jid
                log.info("Admin active: %s (%s)", nick, affiliation)

    # ---------- SYNC BANS ----------
    async def sync_bans(self):
        for room in self.protected_rooms:
            try:
                result = await self.plugin["xep_0045"].get_users_by_affiliation(room, affiliation='outcast')
                for jid in result:
                    async with self.db.execute("SELECT jid FROM bans WHERE jid=?", (jid,)) as cursor:
                        row = await cursor.fetchone()
                    if not row:
                        await self.db.execute(
                            "INSERT INTO bans VALUES (?, ?, ?, ?)", (jid, 0, "sync", None)
                        )
                        await self.db.commit()
                        log.info("Synced ban from %s: %s", room, jid)
                        await self.ban_all(jid, 0, "sync")
            except (IqError, IqTimeout) as e:
                log.error("Failed to sync bans in %s: %s", room, e)

    # ---------- BACKGROUND UNBAN ----------
    async def unban_worker(self):
        while True:
            now = int(time.time())
            async with self.db.execute("SELECT jid FROM bans WHERE until > 0 AND until < ?", (now,)) as cursor:
                rows = await cursor.fetchall()
            for (jid,) in rows:
                await self.unban_all(jid, issuer="system")
            await asyncio.sleep(30)


# ---------- MAIN ----------
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
