"""Protected room invite lifecycle handling."""

from __future__ import annotations

import logging
from xml.etree import ElementTree as ET

from envs_xmpp_core.xmpp.invites import (
    extract_room_invite as core_extract_room_invite,
)
from envs_xmpp_core.xmpp.invites import (
    invite_is_expired as core_invite_is_expired,
)
from envs_xmpp_core.xmpp.invites import (
    inviter_from_attr as core_inviter_from_attr,
)
from envs_xmpp_core.xmpp.invites import (
    reason_from_invite_element as core_reason_from_invite_element,
)
from envs_xmpp_core.xmpp.invites import (
    room_invite_from_direct_plugin as core_room_invite_from_direct_plugin,
)
from envs_xmpp_core.xmpp.invites import (
    room_invite_from_muc_plugin as core_room_invite_from_muc_plugin,
)
from envs_xmpp_core.xmpp.pending_invites import (
    PendingRoomInvite,
    PendingRoomInviteStore,
    PendingRoomInviteStoreResult,
)
from envs_xmpp_core.xmpp.stanza import safe_get_plugin, safe_plugin_value

from config import ADMIN_ROOM

from ..utils import (
    get_list_page_size,
    paginate_lines,
    resolve_page,
    safe_jid,
    wants_all_pages,
    without_all_pages_arg,
)

log = logging.getLogger(__name__)


class _BanBotRoomInviteRepository:
    """Adapt BanBot's aiosqlite connection to the shared invite store."""

    def __init__(self, bot) -> None:
        self.bot = bot

    def available(self) -> bool:
        return getattr(self.bot, "db", None) is not None

    async def setup(self) -> None:
        if not self.available():
            return
        await self.bot.db.execute(
            """
            CREATE TABLE IF NOT EXISTS room_invites (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                room_jid TEXT NOT NULL,
                inviter TEXT NOT NULL,
                reason TEXT,
                created_at INTEGER NOT NULL DEFAULT (strftime('%s','now')),
                UNIQUE(room_jid, inviter)
            )
            """
        )
        await self.bot.db.execute(
            "CREATE INDEX IF NOT EXISTS idx_room_invites_created_at ON room_invites(created_at)"
        )
        await self.bot.db.commit()

    async def load_all(self) -> list[PendingRoomInvite]:
        if not self.available():
            return []
        async with self.bot.db.execute(
            """
            SELECT id, room_jid, inviter, reason, created_at
            FROM room_invites
            ORDER BY id ASC
            """
        ) as cursor:
            rows = await cursor.fetchall()
        return [PendingRoomInvite.from_row(row) for row in rows]

    async def insert_if_absent(
        self,
        room_jid: str,
        inviter: str,
        reason: str,
        created_at: int,
    ) -> PendingRoomInviteStoreResult:
        if not self.available():
            raise RuntimeError("room invite database is unavailable")
        cur = await self.bot.db.execute(
            """
            INSERT INTO room_invites (room_jid, inviter, reason, created_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(room_jid, inviter) DO NOTHING
            """,
            (room_jid, inviter, reason, created_at),
        )
        await self.bot.db.commit()
        async with self.bot.db.execute(
            """
            SELECT id, room_jid, inviter, reason, created_at
            FROM room_invites
            WHERE room_jid = ? AND inviter = ?
            """,
            (room_jid, inviter),
        ) as cursor:
            row = await cursor.fetchone()
        if not row:
            raise RuntimeError(f"could not reload stored room invite for {room_jid} from {inviter}")
        return PendingRoomInviteStoreResult(
            PendingRoomInvite.from_row(row),
            created=cur.rowcount == 1,
        )

    async def get(self, invite_id: int) -> PendingRoomInvite | None:
        if not self.available():
            return None
        async with self.bot.db.execute(
            """
            SELECT id, room_jid, inviter, reason, created_at
            FROM room_invites
            WHERE id = ?
            """,
            (invite_id,),
        ) as cursor:
            row = await cursor.fetchone()
        return PendingRoomInvite.from_row(row) if row else None

    async def delete(self, invite_id: int) -> int:
        if not self.available():
            return 0
        cur = await self.bot.db.execute("DELETE FROM room_invites WHERE id = ?", (invite_id,))
        await self.bot.db.commit()
        return cur.rowcount if cur.rowcount is not None and cur.rowcount >= 0 else 0

    async def delete_many(self, invite_ids) -> int:
        ids = [int(invite_id) for invite_id in invite_ids]
        if not ids or not self.available():
            return 0
        placeholders = ",".join("?" for _ in ids)
        cur = await self.bot.db.execute(
            f"DELETE FROM room_invites WHERE id IN ({placeholders})",
            ids,
        )
        await self.bot.db.commit()
        return cur.rowcount if cur.rowcount is not None and cur.rowcount >= 0 else len(ids)

    async def clear(self) -> int:
        if not self.available():
            return 0
        cur = await self.bot.db.execute("DELETE FROM room_invites")
        await self.bot.db.commit()
        return cur.rowcount if cur.rowcount is not None and cur.rowcount >= 0 else 0


def _pending_invite_store(bot) -> PendingRoomInviteStore:
    """Return the shared store while preserving historical injected state."""
    store = getattr(bot, "_pending_room_invite_store", None)
    if not isinstance(store, PendingRoomInviteStore):
        store = PendingRoomInviteStore(_BanBotRoomInviteRepository(bot))
        bot._pending_room_invite_store = store

    current = getattr(bot, "pending_room_invites", None)
    if isinstance(current, dict) and current is not store.pending:
        store.adopt(current)
    elif not isinstance(current, dict) and current is not store.pending:
        store.adopt({})

    bot.pending_room_invites = store.pending
    bot.pending_room_invite_index = store.index
    bot.next_room_invite_id = store.next_id
    return store


class RoomInviteMixin:

    def init_room_invite_state(self) -> None:
        """Initialize runtime pending invite cache."""
        self.room_invites_enabled = False
        self.room_invite_max_age_days = 30
        store = PendingRoomInviteStore(_BanBotRoomInviteRepository(self))
        self._pending_room_invite_store = store
        self.pending_room_invites = store.pending
        self.pending_room_invite_index = store.index
        self.next_room_invite_id = store.next_id

    def _room_invite_max_age_days(self) -> int:
        """Return configured pending invite max age in days.

        A value of 0 disables automatic expiry.
        """
        value = getattr(self, "room_invite_max_age_days", 30)
        try:
            value = int(value)
        except (TypeError, ValueError):
            value = 30
        return max(0, value)

    def _room_invite_is_expired(self, invite, now: int | None = None) -> bool:
        """Return True when a pending room invite is older than the configured limit."""
        return core_invite_is_expired(
            invite.get("created_at", 0),
            self._room_invite_max_age_days(),
            now=now,
        )

    async def cleanup_expired_room_invites(self, room: str | None = None) -> int:
        """Delete expired pending room invites and optionally report."""
        max_age_days = self._room_invite_max_age_days()
        if max_age_days <= 0:
            if room:
                await self.bot_send_message(
                    mto=room,
                    mbody=(
                        "🧹 Expired pending room invite cleanup completed\n\n"
                        "Invite expiry is disabled (ROOM_INVITE_MAX_AGE_DAYS = 0).\n"
                        "Deleted pending invites: 0"
                    ),
                    mtype="groupchat",
                )
            return 0

        store = _pending_invite_store(self)
        deleted = await store.cleanup_expired(max_age_days=max_age_days)
        self.next_room_invite_id = store.next_id
        if room:
            await self.bot_send_message(
                mto=room,
                mbody=(
                    "🧹 Expired pending room invite cleanup completed\n\n"
                    f"Max age: {max_age_days} day(s)\n"
                    f"Deleted pending invites: {deleted}"
                ),
                mtype="groupchat",
            )
        return deleted

    async def setup_room_invites_db(self) -> None:
        """Create the persistent pending room invite table when needed."""
        await _pending_invite_store(self).setup()

    async def load_pending_room_invites(self) -> None:
        """Load persisted pending room invites into the shared runtime cache."""
        store = _pending_invite_store(self)
        result = await store.load(max_age_days=self._room_invite_max_age_days())
        self.next_room_invite_id = store.next_id
        if result.expired_count:
            log.info("Expired %d pending room invite(s)", result.expired_count)
        if result.active_count:
            log.info("Loaded %d pending room invite(s)", result.active_count)

    async def _store_pending_room_invite(
        self,
        room_jid: str,
        inviter: str,
        reason: str = "",
    ) -> PendingRoomInviteStoreResult:
        """Persist one pending invite and report whether it was newly created."""
        store = _pending_invite_store(self)
        result = await store.store(
            room_jid,
            inviter,
            reason,
            max_age_days=self._room_invite_max_age_days(),
        )
        self.next_room_invite_id = store.next_id
        return result

    async def _delete_pending_room_invite(self, invite_id: int) -> PendingRoomInvite | None:
        """Delete a pending invite from DB and runtime cache."""
        store = _pending_invite_store(self)
        invite = await store.delete(invite_id)
        self.next_room_invite_id = store.next_id
        return invite

    async def cleanup_pending_room_invites(self, room: str) -> None:
        """Delete all pending room invites from DB and runtime cache."""
        store = _pending_invite_store(self)
        count = await store.clear()
        self.next_room_invite_id = store.next_id
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
        return core_reason_from_invite_element(invite_el)

    def _room_invite_inviter_from_attr(self, value: str | None, room_jid: str = "") -> str:
        """Return the best available inviter identity from an invite 'from' value."""
        return core_inviter_from_attr(value, room_jid, jid_bare=self._jid_bare)

    def _safe_get_plugin(self, stanza, plugin_name: str):
        return safe_get_plugin(stanza, plugin_name)

    def _safe_plugin_value(self, plugin, key: str) -> str:
        return safe_plugin_value(plugin, key)

    def _room_invite_from_muc_plugin(self, msg) -> dict[str, str] | None:
        """Extract mediated MUC invites from registered Slixmpp plugins."""
        invite = core_room_invite_from_muc_plugin(
            msg,
            jid_bare=self._jid_bare,
            get_plugin=self._safe_get_plugin,
            plugin_value=self._safe_plugin_value,
        )
        return invite.as_dict() if invite is not None else None

    def _room_invite_from_direct_plugin(self, msg) -> dict[str, str] | None:
        """Extract XEP-0249 direct invites from a registered Slixmpp plugin."""
        invite = core_room_invite_from_direct_plugin(
            msg,
            jid_bare=self._jid_bare,
            get_plugin=self._safe_get_plugin,
            plugin_value=self._safe_plugin_value,
        )
        return invite.as_dict() if invite is not None else None

    def _room_invite_from_plugin(self, msg) -> dict[str, str] | None:
        """Extract invites from registered Slixmpp plugins without warning spam."""
        return self._room_invite_from_muc_plugin(msg) or self._room_invite_from_direct_plugin(msg)

    def _extract_room_invite(self, msg) -> dict[str, str] | None:
        """Extract room/inviter/reason from direct or mediated MUC invite messages."""
        invite = core_extract_room_invite(
            msg,
            jid_bare=self._jid_bare,
            get_plugin=self._safe_get_plugin,
            plugin_value=self._safe_plugin_value,
        )
        return invite.as_dict() if invite is not None else None

    async def handle_room_invite_message(self, msg) -> bool:
        """Handle a MUC invite message if present. Return True when consumed."""
        invite = self._extract_room_invite(msg)
        if not invite:
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
        """Inspect direct/normal message stanzas for MUC invites.

        Do not scan regular groupchat messages here. The dedicated
        groupchat_invite handler covers mediated MUC invites, and scanning every
        groupchat/history message adds unnecessary overhead on busy rooms.
        """
        try:
            msg_type = msg["type"]
        except Exception:
            msg_type = msg.get("type", "")

        if msg_type not in ("chat", "normal"):
            return

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

        stored_result = await self._store_pending_room_invite(room_jid, inviter, reason)
        if not stored_result.created:
            log.info("Ignoring duplicate room invite for %s from %s", room_jid, inviter)
            return
        invite = stored_result.invite

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

    async def _remove_pending_room_invite(self, invite_id: int) -> PendingRoomInvite | None:
        """Remove and return a pending invite by id."""
        return await self._delete_pending_room_invite(invite_id)

    def _room_invite_usage(self) -> str:
        """Return room invite command usage text."""
        p = self.command_prefix
        return (
            "Usage:\n"
            f"  {p}room invite list [all|page|last]\n"
            f"  {p}room invite accept <id>\n"
            f"  {p}room invite decline/remove/delete <id>\n"
            f"  {p}room invite cleanup [expired]"
        )

    async def cmd_room_invite(self, args: list[str], room: str) -> None:
        """Admin command for pending protected-room invites."""
        if not getattr(self, "room_invites_enabled", False):
            await self.bot_send_message(
                mto=room,
                mbody=(
                    "❌ Room invite service is disabled.\n"
                    f"Set ROOM_INVITES_ENABLED = True in config.py and run {getattr(self, 'command_prefix', '!')}reloadconfig."
                ),
                mtype="groupchat",
            )
            return

        if not args or args[0].lower() in {"help", "usage"}:
            await self.bot_send_message(mto=room, mbody=self._room_invite_usage(), mtype="groupchat")
            return

        action = args[0].lower()

        if action == "cleanup":
            if len(args) > 1 and args[1].lower() == "expired":
                await self.cleanup_expired_room_invites(room)
            else:
                await self.cleanup_pending_room_invites(room)
            return

        if action in {"list", "ls"}:
            # Reload from DB so list output is accurate after restart or changes.
            await self.load_pending_room_invites()
            await self.cleanup_expired_room_invites()

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
                per_page = get_list_page_size(self)
                page = resolve_page(page, len(lines), per_page=per_page)
                page_lines, current_page, total_pages, total_items = paginate_lines(lines, page, per_page=per_page)
                text = (
                    f"📨 Pending Room Invites ({total_items}) - Page {current_page}/{total_pages}:\n"
                    + "\n".join(page_lines)
                )
                if current_page < total_pages:
                    text += f"\n\nUse {self.command_prefix}room invite list {current_page + 1} for the next page."

            await self.bot_send_message(mto=room, mbody=text, mtype="groupchat")
            return

        if action in {"accept", "decline", "reject", "remove", "rm", "delete", "del"}:
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

            invite = self.pending_room_invites.get(invite_id)
            if not invite and getattr(self, "db", None):
                await self.load_pending_room_invites()
                invite = self.pending_room_invites.get(invite_id)
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
                if room_jid in getattr(self, "protected_rooms", set()):
                    await self._remove_pending_room_invite(invite_id)
                    await self.bot_send_message(
                        mto=room,
                        mbody=(
                            f"✅ Accepted protected-room invite #{invite_id}.\n"
                            f"Room: {room_jid}\n"
                            "Room is already protected; stale pending invite was removed."
                        ),
                        mtype="groupchat",
                    )
                    return

                await self.cmd_room(["add", room_jid], room)
                if room_jid in getattr(self, "protected_rooms", set()):
                    await self._remove_pending_room_invite(invite_id)
                    await self.bot_send_message(
                        mto=room,
                        mbody=(
                            f"✅ Accepted protected-room invite #{invite_id}.\n"
                            f"Room: {room_jid}\n"
                            f"Invited by: {safe_jid(inviter)}"
                        ),
                        mtype="groupchat",
                    )
                else:
                    await self.bot_send_message(
                        mto=room,
                        mbody=(
                            f"⚠️ Protected-room invite #{invite_id} was not removed because adding the room failed.\n"
                            f"Room: {room_jid}\n"
                            "The invite remains pending."
                        ),
                        mtype="groupchat",
                    )
                return

            invite = await self._remove_pending_room_invite(invite_id)
            if not invite:
                await self.bot_send_message(
                    mto=room,
                    mbody=f"❌ Unknown pending room invite id: {invite_id}",
                    mtype="groupchat",
                )
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
