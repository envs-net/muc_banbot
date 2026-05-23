"""Protected-room invite workflow for admin-reviewed room onboarding."""

from __future__ import annotations

import logging
import time
from xml.etree import ElementTree as ET

from config import ADMIN_ROOM

from .utils import (
    paginate_lines,
    resolve_page,
    safe_jid,
    wants_all_pages,
    without_all_pages_arg,
)

log = logging.getLogger(__name__)

_DIRECT_INVITE_NS = "jabber:x:conference"
_MUC_USER_NS = "http://jabber.org/protocol/muc#user"


class RoomInviteMixin:
    """Collect MUC invites and let admins accept or decline them."""

    def init_room_invite_state(self) -> None:
        """Initialize runtime pending invite cache."""
        self.room_invites_enabled = False
        self.pending_room_invites: dict[int, dict[str, object]] = {}
        self.pending_room_invite_index: dict[tuple[str, str], int] = {}
        self.next_room_invite_id = 1


    async def setup_room_invites_db(self) -> None:
        """Create the persistent pending room invite table when needed."""
        if not getattr(self, "db", None):
            return

        await self.db.execute("""
            CREATE TABLE IF NOT EXISTS room_invites (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                room_jid TEXT NOT NULL,
                inviter TEXT NOT NULL,
                reason TEXT,
                created_at INTEGER NOT NULL DEFAULT (strftime('%s','now')),
                UNIQUE(room_jid, inviter)
            )
        """)
        await self.db.execute(
            "CREATE INDEX IF NOT EXISTS idx_room_invites_created_at ON room_invites(created_at)"
        )
        await self.db.commit()


    async def load_pending_room_invites(self) -> None:
        """Load persisted pending room invites into runtime cache."""
        if not getattr(self, "db", None):
            return

        self.pending_room_invites.clear()
        self.pending_room_invite_index.clear()
        self.next_room_invite_id = 1

        await self.setup_room_invites_db()
        async with self.db.execute(
            """
            SELECT id, room_jid, inviter, reason, created_at
            FROM room_invites
            ORDER BY id ASC
            """
        ) as cursor:
            rows = await cursor.fetchall()

        max_id = 0
        for invite_id, room_jid, inviter, reason, created_at in rows:
            invite_id = int(invite_id)
            room_jid = str(room_jid).lower()
            inviter = str(inviter).lower()
            invite = {
                "id": invite_id,
                "room_jid": room_jid,
                "inviter": inviter,
                "reason": reason or "",
                "created_at": int(created_at or 0),
            }
            self.pending_room_invites[invite_id] = invite
            self.pending_room_invite_index[(room_jid, inviter)] = invite_id
            max_id = max(max_id, invite_id)

        self.next_room_invite_id = max_id + 1
        if rows:
            log.info("Loaded %d pending room invite(s)", len(rows))


    async def _store_pending_room_invite(
        self,
        room_jid: str,
        inviter: str,
        reason: str = "",
    ) -> dict[str, object] | None:
        """Persist a pending invite and add it to runtime cache."""
        key = (room_jid, inviter)
        existing_id = self.pending_room_invite_index.get(key)
        if existing_id is not None and existing_id in self.pending_room_invites:
            return self.pending_room_invites[existing_id]

        created_at = int(time.time())

        if not getattr(self, "db", None):
            invite_id = int(self.next_room_invite_id)
            self.next_room_invite_id += 1
            invite = {
                "id": invite_id,
                "room_jid": room_jid,
                "inviter": inviter,
                "reason": reason or "",
                "created_at": created_at,
            }
            self.pending_room_invites[invite_id] = invite
            self.pending_room_invite_index[key] = invite_id
            return invite

        await self.setup_room_invites_db()
        try:
            cur = await self.db.execute(
                """
                INSERT INTO room_invites (room_jid, inviter, reason, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (room_jid, inviter, reason or "", created_at),
            )
            await self.db.commit()
            invite_id = int(cur.lastrowid)
        except Exception:
            # The UNIQUE(room_jid, inviter) constraint means this is most likely
            # a duplicate invite that was persisted before a restart. Load it
            # and reuse the existing pending id.
            async with self.db.execute(
                """
                SELECT id, reason, created_at
                FROM room_invites
                WHERE room_jid = ? AND inviter = ?
                """,
                (room_jid, inviter),
            ) as cursor:
                row = await cursor.fetchone()
            if not row:
                raise
            invite_id, stored_reason, stored_created_at = row
            invite_id = int(invite_id)
            reason = stored_reason or reason or ""
            created_at = int(stored_created_at or created_at)

        invite = {
            "id": invite_id,
            "room_jid": room_jid,
            "inviter": inviter,
            "reason": reason or "",
            "created_at": created_at,
        }
        self.pending_room_invites[invite_id] = invite
        self.pending_room_invite_index[key] = invite_id
        self.next_room_invite_id = max(int(getattr(self, "next_room_invite_id", 1)), invite_id + 1)
        return invite


    async def _delete_pending_room_invite(self, invite_id: int) -> dict[str, object] | None:
        """Delete a pending invite from DB and runtime cache."""
        invite = self.pending_room_invites.pop(invite_id, None)

        if getattr(self, "db", None):
            await self.setup_room_invites_db()
            if invite is None:
                async with self.db.execute(
                    """
                    SELECT id, room_jid, inviter, reason, created_at
                    FROM room_invites
                    WHERE id = ?
                    """,
                    (invite_id,),
                ) as cursor:
                    row = await cursor.fetchone()
                if row:
                    _id, room_jid, inviter, reason, created_at = row
                    invite = {
                        "id": int(_id),
                        "room_jid": str(room_jid).lower(),
                        "inviter": str(inviter).lower(),
                        "reason": reason or "",
                        "created_at": int(created_at or 0),
                    }

            await self.db.execute("DELETE FROM room_invites WHERE id = ?", (invite_id,))
            await self.db.commit()

        if invite:
            key = (str(invite["room_jid"]), str(invite["inviter"]))
            self.pending_room_invite_index.pop(key, None)
        return invite


    async def cleanup_pending_room_invites(self, room: str) -> None:
        """Delete all pending room invites from DB and runtime cache."""
        count = len(self.pending_room_invites)
        if getattr(self, "db", None):
            await self.setup_room_invites_db()
            cur = await self.db.execute("DELETE FROM room_invites")
            await self.db.commit()
            count = cur.rowcount if cur.rowcount is not None and cur.rowcount >= 0 else count

        self.pending_room_invites.clear()
        self.pending_room_invite_index.clear()
        self.next_room_invite_id = 1

        await self.bot_send_message(
            mto=room,
            mbody=(
                "🧹 Pending room invite cleanup completed\n\n"
                f"Deleted pending invites: {count}"
            ),
            mtype="groupchat",
        )


    def _jid_bare(self, value) -> str:
        """Return a best-effort bare JID string from a stanza value."""
        if value is None:
            return ""

        bare = getattr(value, "bare", None)
        if bare:
            return str(bare).lower()

        return self.bare_jid(str(value)) or ""


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
        """Return a registered stanza plugin without triggering unknown-interface warnings."""
        get_plugin = getattr(stanza, "get_plugin", None)
        if not callable(get_plugin):
            return None

        try:
            return get_plugin(plugin_name, check=True)
        except TypeError:
            try:
                return get_plugin(plugin_name)
            except Exception:
                return None
        except Exception:
            return None


    def _safe_plugin_value(self, plugin, key: str) -> str:
        """Return a string value from a stanza plugin."""
        if plugin is None:
            return ""

        try:
            value = plugin.get(key)
        except Exception:
            try:
                value = plugin[key]
            except Exception:
                return ""

        if value is None:
            return ""

        return str(value).strip()


    def _room_invite_from_muc_plugin(self, msg) -> dict[str, str] | None:
        """
        Extract mediated MUC invites from Slixmpp's registered muc/invite plugin.

        This avoids msg["invite"] probing, which can emit root logger warnings when
        the stanza interface is not registered.
        """
        muc = self._safe_get_plugin(msg, "muc")
        if muc is None:
            return None

        invite = self._safe_get_plugin(muc, "invite")
        if invite is None:
            return None

        room_jid = self._jid_bare(msg["from"])
        if not room_jid:
            return None

        inviter = self._room_invite_inviter_from_attr(
            self._safe_plugin_value(invite, "from"),
            room_jid,
        )
        if not inviter:
            inviter = "unknown"

        reason = self._safe_plugin_value(invite, "reason")

        return {
            "room_jid": room_jid,
            "inviter": inviter,
            "reason": reason,
        }


    def _room_invite_from_direct_plugin(self, msg) -> dict[str, str] | None:
        """
        Extract XEP-0249 direct invites from a registered plugin if available.

        XML parsing remains preferred because it works without optional stanza
        plugin registration. This fallback is used for Slixmpp's
        groupchat_direct_invite event payloads.
        """
        direct = self._safe_get_plugin(msg, "groupchat_invite")
        if direct is None:
            direct = self._safe_get_plugin(msg, "conference")

        if direct is None:
            return None

        room_jid = (
            self._safe_plugin_value(direct, "jid")
            or self._safe_plugin_value(direct, "room")
            or self._safe_plugin_value(direct, "to")
        ).lower()

        if not room_jid:
            return None

        inviter = self._jid_bare(msg["from"]) or "unknown"
        reason = self._safe_plugin_value(direct, "reason")

        return {
            "room_jid": room_jid,
            "inviter": inviter,
            "reason": reason,
        }


    def _room_invite_from_plugin(self, msg) -> dict[str, str] | None:
        """Extract invites from registered Slixmpp plugins without warning spam."""
        return (
            self._room_invite_from_muc_plugin(msg)
            or self._room_invite_from_direct_plugin(msg)
        )


    def _extract_room_invite(self, msg) -> dict[str, str] | None:
        """Extract room/inviter/reason from direct or mediated MUC invite messages."""
        xml = getattr(msg, "xml", None)
        if xml is None:
            return self._room_invite_from_plugin(msg)

        # XEP-0249 direct invite:
        # <x xmlns='jabber:x:conference' jid='room@conference'>
        for direct in xml.findall(f".//{{{_DIRECT_INVITE_NS}}}x"):
            room_jid = (direct.attrib.get("jid") or "").strip().lower()
            if not room_jid:
                continue

            inviter = self._jid_bare(msg["from"]) or "unknown"
            return {
                "room_jid": room_jid,
                "inviter": inviter,
                "reason": (direct.attrib.get("reason") or "").strip(),
            }

        # XEP-0045 mediated invite:
        # message from the room with
        # <x xmlns='http://jabber.org/protocol/muc#user'>
        #   <invite from='user@example.org'/>
        # </x>
        for invite in xml.findall(f".//{{{_MUC_USER_NS}}}invite"):
            room_jid = self._jid_bare(msg["from"])
            if not room_jid:
                continue

            inviter = self._room_invite_inviter_from_attr(
                invite.attrib.get("from"),
                room_jid,
            )
            if not inviter:
                inviter = "unknown"

            return {
                "room_jid": room_jid,
                "inviter": inviter,
                "reason": self._room_invite_reason_from_invite(invite),
            }

        return self._room_invite_from_plugin(msg)


    async def handle_room_invite_message(self, msg) -> bool:
        """Handle a MUC invite message if present. Return True when consumed."""
        invite = self._extract_room_invite(msg)
        if not invite:
            log.debug(
                "Room invite handler saw message without invite payload: from=%s type=%s",
                msg.get("from"),
                msg.get("type"),
            )
            return False

        log.info(
            "Room invite detected: room=%s inviter=%s",
            invite["room_jid"],
            invite["inviter"],
        )

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


    async def on_room_invite_message(self, msg) -> None:
        """Inspect normal message stanzas for direct or mediated MUC invites."""
        await self.handle_room_invite_message(msg)


    async def on_room_invite(self, msg) -> None:
        """Event handler wrapper for Slixmpp MUC invite events."""
        handled = await self.handle_room_invite_message(msg)
        if not handled:
            log.warning(
                "Room invite event received but no room JID could be extracted: %s",
                getattr(msg, "xml", msg),
            )


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

        invite = await self._store_pending_room_invite(room_jid, inviter, reason)
        if not invite:
            log.info("Ignoring duplicate room invite for %s from %s", room_jid, inviter)
            return

        reason_line = f"\nReason: {reason}" if reason else ""
        p = self.command_prefix
        await self.bot_send_message(
            mto=ADMIN_ROOM,
            mbody=(
                "📨 New protected-room invite\n"
                f"ID: {invite['id']}\n"
                f"Room: {room_jid}\n"
                f"Invited by: {safe_jid(inviter)}"
                f"{reason_line}\n\n"
                f"Accept: {p}room invite accept {invite['id']}\n"
                f"Decline: {p}room invite decline {invite['id']}"
            ),
            mtype="groupchat",
        )


    async def _remove_pending_room_invite(self, invite_id: int) -> dict[str, object] | None:
        """Remove and return a pending invite by id."""
        return await self._delete_pending_room_invite(invite_id)


    def _room_invite_usage(self) -> str:
        """Return room invite command usage text."""
        p = self.command_prefix
        return (
            "Usage:\n"
            f"  {p}room invite list [all|page|last]\n"
            f"  {p}room invite accept <id>\n"
            f"  {p}room invite decline <id>\n"
            f"  {p}room invite cleanup"
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

        if action == "cleanup":
            await self.cleanup_pending_room_invites(room)
            return

        if action in {"list", "ls"}:
            # Reload from DB so list output is accurate after restart or changes.
            await self.load_pending_room_invites()

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
                await self.bot_send_message(
                    mto=room,
                    mbody="📨 Pending Room Invites:\nNo pending invites.",
                    mtype="groupchat",
                )
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
                await self.bot_send_message(
                    mto=room,
                    mbody=f"❌ Usage: {self.command_prefix}room invite {action} <id>",
                    mtype="groupchat",
                )
                return
            try:
                invite_id = int(args[1])
            except ValueError:
                await self.bot_send_message(mto=room, mbody="❌ Invite id must be a number.", mtype="groupchat")
                return

            invite = await self._remove_pending_room_invite(invite_id)
            if not invite:
                await self.bot_send_message(
                    mto=room,
                    mbody=f"❌ Unknown pending room invite id: {invite_id}",
                    mtype="groupchat",
                )
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
