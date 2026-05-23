"""Protected-room invite workflow for admin-reviewed room onboarding."""

from __future__ import annotations

import logging
import time
from xml.etree import ElementTree as ET

from config import ADMIN_ROOM

from .utils import paginate_lines, resolve_page, safe_jid, wants_all_pages, without_all_pages_arg

log = logging.getLogger(__name__)

_DIRECT_INVITE_NS = "jabber:x:conference"
_MUC_USER_NS = "http://jabber.org/protocol/muc#user"


class RoomInviteMixin:
    """Collect MUC invites and let admins accept or decline them."""

    def init_room_invite_state(self) -> None:
        """Initialize runtime-only pending invite state."""
        self.room_invites_enabled = False
        self.pending_room_invites: dict[int, dict[str, object]] = {}
        self.pending_room_invite_index: dict[tuple[str, str], int] = {}
        self.next_room_invite_id = 1


    def _jid_bare(self, value) -> str:
        """Return a best-effort bare JID string from a stanza value."""
        if value is None:
            return ""
        bare = getattr(value, "bare", None)
        if bare:
            return str(bare).lower()
        return self.bare_jid(str(value)) or ""


    def _jid_full(self, value) -> str:
        """Return a best-effort full JID string from a stanza value."""
        return str(value or "")


    def _room_invite_reason_from_invite(self, invite_el: ET.Element) -> str:
        """Extract an optional mediated invite reason."""
        reason_el = invite_el.find(f"{{{_MUC_USER_NS}}}reason")
        if reason_el is not None and reason_el.text:
            return reason_el.text.strip()
        return ""


    def _room_invite_inviter_from_attr(self, value: str | None, room_jid: str = "") -> str:
        """Return the best available inviter identity from an invite 'from' value."""
        raw = str(value or "").strip()
        if not raw:
            return ""

        bare = self._jid_bare(raw)

        # If the server only gives us a MUC occupant JID, keep the full
        # room/nick instead of collapsing it to the room bare JID.
        if room_jid and bare == room_jid and "/" in raw:
            return raw.lower()

        return bare or raw.lower()


    def _safe_get_plugin(self, stanza, plugin_name: str):
        """Return a registered stanza plugin without probing unknown interfaces."""
        getter = getattr(stanza, "get_plugin", None)
        if not callable(getter):
            return None

        try:
            return getter(plugin_name, check=True)
        except TypeError:
            try:
                return getter(plugin_name)
            except Exception:
                return None
        except Exception:
            return None


    def _room_invite_plugin_value(self, plugin, key: str) -> str:
        """Best-effort read of an interface value from a known plugin object."""
        if plugin is None:
            return ""

        try:
            value = plugin.get(key)
        except Exception:
            try:
                value = plugin[key]
            except Exception:
                value = ""

        return str(value or "").strip()


    def _room_invite_plugin_values(self, msg) -> dict[str, str] | None:
        """Extract invite data from registered Slixmpp plugins safely."""
        # XEP-0249 direct invites can be exposed as the groupchat_invite
        # plugin when xep_0249 is registered. Do not use msg[...] here:
        # unknown stanza interfaces emit root logger warnings.
        direct = self._safe_get_plugin(msg, "groupchat_invite")
        if direct is not None:
            room_jid = self._room_invite_plugin_value(direct, "jid").lower()
            if room_jid:
                return {
                    "room_jid": room_jid,
                    "inviter": self._jid_bare(msg["from"]) or "unknown",
                    "reason": self._room_invite_plugin_value(direct, "reason"),
                }

        # XEP-0045 mediated invites are registered by Slixmpp directly on
        # MUCMessage as the invite plugin. This is what the groupchat_invite
        # event usually carries.
        invite = self._safe_get_plugin(msg, "invite")
        if invite is None:
            # Defensive fallback for older/custom Slixmpp variants which may
            # expose the invite below a MUC-user plugin.
            muc = self._safe_get_plugin(msg, "muc")
            invite = self._safe_get_plugin(muc, "invite") if muc is not None else None

        if invite is not None:
            room_jid = self._jid_bare(msg["from"])
            if room_jid:
                inviter = self._room_invite_inviter_from_attr(
                    self._room_invite_plugin_value(invite, "from"),
                    room_jid,
                ) or "unknown"
                return {
                    "room_jid": room_jid,
                    "inviter": inviter,
                    "reason": self._room_invite_plugin_value(invite, "reason"),
                }

        return None


    def _extract_room_invite(self, msg) -> dict[str, str] | None:
        """Extract room/inviter/reason from direct or mediated MUC invite messages."""
        xml = getattr(msg, "xml", None)
        if xml is None:
            return self._room_invite_plugin_values(msg)

        # XEP-0249 direct invite: <x xmlns='jabber:x:conference' jid='room@conference'>
        for direct in xml.findall(f".//{{{_DIRECT_INVITE_NS}}}x"):
            room_jid = (direct.attrib.get("jid") or "").strip().lower()
            if not room_jid:
                continue
            inviter = self._jid_bare(msg["from"])
            return {
                "room_jid": room_jid,
                "inviter": inviter,
                "reason": (direct.attrib.get("reason") or "").strip(),
            }

        # XEP-0045 mediated invite: message from the room with
        # <x xmlns='http://jabber.org/protocol/muc#user'><invite from='...'/></x>
        for invite in xml.findall(f".//{{{_MUC_USER_NS}}}invite"):
            room_jid = self._jid_bare(msg["from"])
            if not room_jid:
                continue

            inviter = self._room_invite_inviter_from_attr(invite.attrib.get("from"), room_jid)
            if not inviter:
                inviter = "unknown"

            return {
                "room_jid": room_jid,
                "inviter": inviter,
                "reason": self._room_invite_reason_from_invite(invite),
            }

        return self._room_invite_plugin_values(msg)

    async def handle_room_invite_message(self, msg) -> bool:
        """Handle a MUC invite message if present. Return True when consumed."""
        invite = self._extract_room_invite(msg)
        if not invite:
            return False

        if not getattr(self, "room_invites_enabled", False):
            log.info(
                "Room invite service disabled; ignoring invite for %s from %s",
                invite["room_jid"],
                invite["inviter"],
            )
            return True

        await self._handle_pending_room_invite(
            invite["room_jid"],
            invite["inviter"],
            invite.get("reason", ""),
        )
        return True


    async def on_room_invite(self, msg) -> None:
        """Event handler wrapper for Slixmpp MUC invite events."""
        handled = await self.handle_room_invite_message(msg)
        if not handled:
            log.warning("Room invite event received but no room JID could be extracted: %s", getattr(msg, "xml", msg))


    async def _handle_pending_room_invite(self, room_jid: str, inviter: str, reason: str = "") -> None:
        """Validate and store a pending invite, then announce it in the admin room."""
        room_jid = (room_jid or "").strip().lower()
        inviter = (inviter or "unknown").strip().lower()

        is_valid, error_msg = await self.validate_room_jid(room_jid)
        if not is_valid:
            log.warning("Ignoring invalid room invite for %s from %s: %s", room_jid, inviter, error_msg)
            await self.bot_send_message(
                mto=ADMIN_ROOM,
                mbody=(
                    "⚠️ Ignored invalid protected-room invite.\n"
                    f"Room: {room_jid or '<missing>'}\n"
                    f"Invited by: {safe_jid(inviter)}\n"
                    f"Reason: {error_msg}"
                ),
                mtype="groupchat",
            )
            return

        if room_jid in getattr(self, "protected_rooms", set()):
            log.info("Ignoring invite for already protected room %s from %s", room_jid, inviter)
            return

        key = (room_jid, inviter)
        existing_id = self.pending_room_invite_index.get(key)
        if existing_id is not None and existing_id in self.pending_room_invites:
            log.info("Ignoring duplicate room invite for %s from %s", room_jid, inviter)
            return

        invite_id = int(self.next_room_invite_id)
        self.next_room_invite_id += 1
        invite = {
            "id": invite_id,
            "room_jid": room_jid,
            "inviter": inviter,
            "reason": reason or "",
            "created_at": int(time.time()),
        }
        self.pending_room_invites[invite_id] = invite
        self.pending_room_invite_index[key] = invite_id

        reason_line = f"\nReason: {reason}" if reason else ""
        p = self.command_prefix
        await self.bot_send_message(
            mto=ADMIN_ROOM,
            mbody=(
                "📨 New protected-room invite\n"
                f"ID: {invite_id}\n"
                f"Room: {room_jid}\n"
                f"Invited by: {safe_jid(inviter)}"
                f"{reason_line}\n\n"
                f"Accept: {p}room invite accept {invite_id}\n"
                f"Decline: {p}room invite decline {invite_id}"
            ),
            mtype="groupchat",
        )


    def _remove_pending_room_invite(self, invite_id: int) -> dict[str, object] | None:
        """Remove and return a pending invite by id."""
        invite = self.pending_room_invites.pop(invite_id, None)
        if not invite:
            return None

        key = (str(invite["room_jid"]), str(invite["inviter"]))
        self.pending_room_invite_index.pop(key, None)
        return invite


    def _room_invite_usage(self) -> str:
        """Return room invite command usage text."""
        p = self.command_prefix
        return (
            "Usage:\n"
            f"  {p}room invite list [all|page|last]\n"
            f"  {p}room invite accept <id>\n"
            f"  {p}room invite decline <id>"
        )


    async def cmd_room_invite(self, args: list[str], room: str) -> None:
        """Admin command for pending protected-room invites."""
        if not getattr(self, "room_invites_enabled", False):
            await self.bot_send_message(
                mto=room,
                mbody=(
                    "❌ Room invite service is disabled.\n"
                    "Set ROOM_INVITES_ENABLED = True in config.py and run !reloadconfig."
                ),
                mtype="groupchat",
            )
            return

        if not args or args[0].lower() in {"help", "usage"}:
            await self.bot_send_message(mto=room, mbody=self._room_invite_usage(), mtype="groupchat")
            return

        action = args[0].lower()

        if action in {"list", "ls"}:
            show_all = wants_all_pages(args[1:])
            list_args = without_all_pages_arg(args[1:])
            page = 1
            if list_args:
                if list_args[0].lower() == "last":
                    page = -1
                else:
                    try:
                        page = max(1, int(list_args[0]))
                    except ValueError:
                        await self.bot_send_message(
                            mto=room,
                            mbody=f"❌ Usage: {self.command_prefix}room invite list [all|page|last]",
                            mtype="groupchat",
                        )
                        return

            invites = [self.pending_room_invites[key] for key in sorted(self.pending_room_invites)]
            if not invites:
                await self.bot_send_message(mto=room, mbody="📨 Pending Room Invites:\nNo pending invites.", mtype="groupchat")
                return

            lines = [
                f"#{invite['id']} {invite['room_jid']} — invited by {safe_jid(invite['inviter'])}"
                + (f" — {invite['reason']}" if invite.get("reason") else "")
                for invite in invites
            ]

            if show_all:
                text = f"📨 Pending Room Invites ({len(lines)}) - All:\n" + "\n".join(lines)
            else:
                page = resolve_page(page, len(lines), per_page=10)
                page_lines, current_page, total_pages, total_items = paginate_lines(lines, page, per_page=10)
                text = (
                    f"📨 Pending Room Invites ({total_items}) - Page {current_page}/{total_pages}:\n"
                    + "\n".join(page_lines)
                )
                if current_page < total_pages:
                    text += f"\n\nUse {self.command_prefix}room invite list {current_page + 1} for the next page."

            await self.bot_send_message(mto=room, mbody=text, mtype="groupchat")
            return

        if action in {"accept", "decline", "reject"}:
            if len(args) < 2:
                await self.bot_send_message(mto=room, mbody=f"❌ Usage: {self.command_prefix}room invite {action} <id>", mtype="groupchat")
                return
            try:
                invite_id = int(args[1])
            except ValueError:
                await self.bot_send_message(mto=room, mbody="❌ Invite id must be a number.", mtype="groupchat")
                return

            invite = self._remove_pending_room_invite(invite_id)
            if not invite:
                await self.bot_send_message(mto=room, mbody=f"❌ Unknown pending room invite id: {invite_id}", mtype="groupchat")
                return

            room_jid = str(invite["room_jid"])
            inviter = str(invite["inviter"])

            if action == "accept":
                await self.bot_send_message(
                    mto=room,
                    mbody=(
                        f"✅ Accepted protected-room invite #{invite_id}.\n"
                        f"Room: {room_jid}\n"
                        f"Invited by: {safe_jid(inviter)}"
                    ),
                    mtype="groupchat",
                )
                await self.cmd_room(["add", room_jid], room)
                return

            await self.bot_send_message(
                mto=room,
                mbody=(
                    f"✅ Declined protected-room invite #{invite_id}.\n"
                    f"Room: {room_jid}\n"
                    f"Invited by: {safe_jid(inviter)}"
                ),
                mtype="groupchat",
            )
            return

        await self.bot_send_message(
            mto=room,
            mbody=f"❌ Unknown room invite action: {action}\n{self._room_invite_usage()}",
            mtype="groupchat",
        )
