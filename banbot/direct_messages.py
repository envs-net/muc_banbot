"""Direct-message and MUC-PM rejection policy."""

from config import ADMIN_ROOM


class DirectMessageMixin:
    async def on_direct_message(self, msg) -> None:
        """
        Reject direct messages (regular DMs and MUC PMs).
        MUC PM admin detection is based on room + nick from the MUC occupant cache.
        """
        # Ignore own messages
        if msg["from"].bare == self.boundjid.bare:
            return

        # Only process direct messages
        if msg["type"] not in ("chat", "normal"):
            return

        sender = msg["from"].bare
        sender_full = str(msg["from"])
        sender_resource = msg["from"].resource

        known_rooms = self.protected_rooms | {ADMIN_ROOM}

        # A real MUC PM looks like: room@conference.example/Nick
        is_muc_pm = sender in known_rooms and sender_resource is not None

        is_admin = False

        if is_muc_pm:
            room = sender
            nick = sender_resource
            info = self.occupants.get(room, {}).get(nick)

            if info and info.get("affiliation") in ("owner", "admin"):
                is_admin = True

            # Optional fallback: if the PM came from another known room,
            # also check whether this user's real JID is admin in ADMIN_ROOM.
            real_jid = info.get("jid") if info else None
            real_bare = self.bare_jid(real_jid) if real_jid else None

            if not is_admin and real_bare:
                for admin_info in self.occupants.get(ADMIN_ROOM, {}).values():
                    admin_jid = admin_info.get("jid")
                    if (
                        admin_jid
                        and self.bare_jid(admin_jid) == real_bare
                        and admin_info.get("affiliation") in ("owner", "admin")
                    ):
                        is_admin = True
                        break

        else:
            # Regular direct DM: user@example/resource or user@example
            sender_bare = self.bare_jid(str(msg["from"]))

            for admin_info in self.occupants.get(ADMIN_ROOM, {}).values():
                admin_jid = admin_info.get("jid")
                if (
                    admin_jid
                    and self.bare_jid(admin_jid) == sender_bare
                    and admin_info.get("affiliation") in ("owner", "admin")
                ):
                    is_admin = True
                    break

        if is_admin:
            response = (
                "🤖 Nice try, admin! But I only take commands directly in the admin room. "
                f"Please use {ADMIN_ROOM}.\nSee you there! 😉"
            )
        else:
            response = (
                "❌ I'm a ban management bot and only operate in designated rooms. "
                "I only listen to admins."
            )

        await self.bot_send_message(
            mto=sender_full if is_muc_pm else sender,
            mbody=response,
            mtype="chat"
        )
