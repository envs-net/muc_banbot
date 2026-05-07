"""Admin/owner permission checks, ban protection, and !whoami."""

import asyncio
import logging

from config import ADMIN_ROOM, NICK
from slixmpp.exceptions import IqError, IqTimeout

from .utils import domain_matches

log = logging.getLogger(__name__)


class AdminMixin:
    def is_admin_or_owner(self, room: str, nick: str | None = None, jid: str | None = None) -> bool:
        """Check if a user is admin or owner in a room using the live occupant cache."""
        occ = self.occupants.get(room, {})
        for n, info in occ.items():
            if nick and n.lower() == nick.lower():
                return info.get("affiliation") in ("owner", "admin")
            if jid and info.get("jid") and self.bare_jid(info["jid"]) == self.bare_jid(jid):
                return info.get("affiliation") in ("owner", "admin")
        return False


    def is_bot_admin_or_owner(self, room: str) -> bool:
        """Check if the bot itself is admin or owner in a given room."""
        occ = self.occupants.get(room, {})
        bot_info = occ.get(NICK)

        if not bot_info:
            log.warning("⚠️ Bot nick not found in occupants for room %s", room)
            return False

        return bot_info.get("affiliation") in ("owner", "admin")


    def is_authorized(self, msg) -> bool:
        """Check if a message sender is authorized to issue admin commands."""
        if msg["from"].bare != ADMIN_ROOM:
            return False
        info = self.occupants.get(ADMIN_ROOM, {}).get(msg["mucnick"])
        return info and info.get("affiliation") in ("owner", "admin")


    async def verify_admin_rights(self, room: str) -> bool:
        """
        Server-side check if the bot is actually admin/owner.
        Returns True if yes, False otherwise.
        """
        try:
            owners = await self.plugin["xep_0045"].get_users_by_affiliation(room, "owner")
            admins = await self.plugin["xep_0045"].get_users_by_affiliation(room, "admin")
            bare_bot_jid = self.boundjid.bare

            for jid in owners + admins:
                if str(jid).split("/")[0].lower() == bare_bot_jid.lower():
                    return True
            return False
        except (IqError, IqTimeout) as e:
            log.warning("Server check failed for %s: %s", room, e)
            return False


    async def get_room_admin_owner_jids(self, room: str) -> set[str]:
        """Return bare JIDs with owner/admin affiliation from the server.

        Some MUC services only allow owners to query affiliation lists. If the
        server rejects the query with <forbidden/>, remember that room and fall
        back to the live occupant cache instead of logging the same warning on
        every ban command.
        """
        protected: set[str] = set()

        if room in self.admin_affiliation_query_forbidden_rooms:
            return protected

        try:
            owners = await self.plugin["xep_0045"].get_users_by_affiliation(room, "owner")
            admins = await self.plugin["xep_0045"].get_users_by_affiliation(room, "admin")
            for jid in owners + admins:
                bare = self.bare_jid(str(jid))
                if bare:
                    protected.add(bare)
        except IqError as e:
            if "forbidden" in str(e):
                self.admin_affiliation_query_forbidden_rooms.add(room)
                log.warning(
                    "Server forbids owner/admin affiliation queries for %s; "
                    "admin protection falls back to live occupant cache for this room.",
                    room,
                )
            else:
                log.warning("Could not fetch admin/owner list for %s: %s", room, e)
        except IqTimeout as e:
            log.warning("Could not fetch admin/owner list for %s: %s", room, e)

        return protected


    async def is_protected_admin_target(
        self,
        target: str,
        nick: str | None = None,
        jid: str | None = None,
    ) -> tuple[bool, str | None]:
        """
        Return (True, reason) if the target would ban an owner/admin in any
        protected room or the admin room.
        """
        target = target.lower().strip()
        bare_target_jid = self.bare_jid(jid or target) if "@" in target or jid else None
        domain_target = target[2:].strip(".") if target.startswith("*.") else None
        rooms_to_check = set(self.protected_rooms) | {ADMIN_ROOM}

        for room in rooms_to_check:
            if nick and self.is_admin_or_owner(room, nick=nick):
                return True, f"{nick} is admin/owner in {room}"
            if bare_target_jid and self.is_admin_or_owner(room, jid=bare_target_jid):
                return True, f"{bare_target_jid} is admin/owner in {room}"

            protected_jids = await self.get_room_admin_owner_jids(room)

            if bare_target_jid and bare_target_jid in protected_jids:
                return True, f"{bare_target_jid} is admin/owner in {room}"

            if domain_target:
                for protected_jid in protected_jids:
                    protected_domain = protected_jid.split("@", 1)[1] if "@" in protected_jid else None
                    if domain_matches(protected_domain, domain_target):
                        return True, f"domain ban *.{domain_target} would include admin/owner {protected_jid} in {room}"

        return False, None


    async def check_bot_admin_rights(self) -> None:
        """
        Check all protected rooms after startup and report where the bot lacks
        admin/owner rights.
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


    async def _cmd_whoami(self, room: str, nick: str) -> None:
        """Show the caller's current MUC affiliation, role, JID, and permissions."""
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
