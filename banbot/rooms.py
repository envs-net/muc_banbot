"""Protected-room management commands and room JID validation."""

import asyncio
import logging
import re

from config import ADMIN_ROOM, NICK
from slixmpp.exceptions import IqError, IqTimeout

from .utils import paginate_lines, wants_all_pages, without_all_pages_arg

log = logging.getLogger(__name__)


class RoomMixin:
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
            show_all = wants_all_pages(args[1:])
            list_args = without_all_pages_arg(args[1:])
            page = 1
            if list_args:
                try:
                    page = max(1, int(list_args[0]))
                except ValueError:
                    await self.bot_send_message(
                        mto=room,
                        mbody=f"❌ Usage: {self.command_prefix}room list [all|page]",
                        mtype="groupchat"
                    )
                    return

            if self.protected_rooms:
                rooms = sorted(self.protected_rooms)
                if show_all:
                    page_lines = rooms
                    total_items = len(rooms)
                    text = (
                        f"🔒 Protected Rooms ({total_items}) - All:\n"
                        + "\n".join(page_lines)
                    )
                else:
                    page_lines, current_page, total_pages, total_items = paginate_lines(rooms, page, per_page=10)

                    text = (
                        f"🔒 Protected Rooms ({total_items}) - Page {current_page}/{total_pages}:\n"
                        + "\n".join(page_lines)
                    )

                    if current_page < total_pages:
                        text += f"\n\nUse {self.command_prefix}room list {current_page + 1} for the next page."
            else:
                text = "🔒 Protected Rooms:\nNo protected rooms."

            await self.bot_send_message(mto=room, mbody=text, mtype="groupchat")

        elif action in ("add", "remove") and len(args) >= 2:
            target = args[1].lower()

            if action == "add":
                # --- Validate Room JID before adding ---
                is_valid, error_msg = await self.validate_room_jid(target)
                if not is_valid:
                    await self.bot_send_message(mto=room, mbody=error_msg, mtype="groupchat")
                    log.warning("Room validation failed for %s: %s", target, error_msg)
                    return

                if target not in self.protected_rooms:
                    # --- In-Memory and DB ---
                    self.protected_rooms.add(target)
                    await self.db.execute("INSERT OR REPLACE INTO rooms (room) VALUES (?)", (target,))
                    await self.db.commit()
                    await self.bot_send_message(mto=room, mbody=f"✅ Room added: {target}", mtype="groupchat")

                    # --- Event handler for new occupants ---
                    if target not in self.registered_rooms:
                        self.add_event_handler(f"muc::{target}::got_online", self.muc_online)
                        self.add_event_handler(f"muc::{target}::got_offline", self.muc_offline)
                        self.registered_rooms.add(target)

                    self.plugin["xep_0045"].join_muc(target, NICK)

                    # --- Ensure the bot itself is online ---
                    async def wait_for_bot_online():
                        for _ in range(10):
                            occ = self.occupants.get(target, {})
                            if NICK in occ:
                                break
                            await asyncio.sleep(1)

                        # Sync regular bans
                        await self.sync_bans_to_rooms_for_single_room(target)

                        # Scan current occupants against RTBL
                        if getattr(self, "rtbl_enabled", False):
                            occ = self.occupants.get(target, {})
                            for occ_nick, info in list(occ.items()):
                                jid = info.get("jid")
                                if jid:
                                    await self.check_jid_against_rtbl(jid, occ_nick)

                        # Apply existing bans to other rooms
                        other_rooms = self.protected_rooms - {target}
                        if other_rooms:
                            log.info("🔄 Applying existing bans to other rooms due to new room addition")
                            await self.bot_send_message(
                                mto=ADMIN_ROOM,
                                mbody=f"🔄 Applying existing bans to other rooms due to new room addition",
                                mtype="groupchat"
                            )
                            for room in other_rooms:
                                await self.sync_bans_to_rooms_for_single_room(room)

                    await wait_for_bot_online()
                else:
                    await self.bot_send_message(mto=room, mbody=f"⚠️ Room already in protected list: {target}", mtype="groupchat")

            elif action == "remove":
                self.protected_rooms.discard(target)
                await self.db.execute("DELETE FROM rooms WHERE room=?", (target,))
                await self.db.commit()
                await self.bot_send_message(mto=room, mbody=f"✅ Room removed: {target}", mtype="groupchat")

                # --- Bot leaves the room immediately ---
                try:
                    self.plugin["xep_0045"].leave_muc(target, NICK)
                except Exception as e:
                    log.warning("⚠️ Failed to leave room %s: %s", target, e)
