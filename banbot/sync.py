"""Room and ban synchronization between SQLite state and MUC affiliation state."""

import asyncio
import logging
import time

from config import ADMIN_ROOM, NICK

log = logging.getLogger(__name__)

from .locks import ban_state_lock


class SyncMixin:
    async def sync_rooms_and_bans(self) -> None:
        """Run full room/ban sync while holding the shared ban-state lock."""
        async with ban_state_lock(self):
            await self._sync_rooms_and_bans_locked()

    async def _sync_rooms_and_bans_locked(self) -> None:
        """
        Full sync for !sync command:
        - Rejoin all protected rooms
        - Check bot admin/owner rights
        - Apply all active bans (only missing ones)
        - Skip expired temporary bans automatically
        Batches rooms to respect semaphore limits and prevent overwhelming server
        """
        now = int(time.time())
        total_rooms = len(self.protected_rooms)
        if total_rooms == 0:
            await self.bot_send_message(
                mto=ADMIN_ROOM,
                mbody="⚠️ No protected rooms to sync.",
                mtype="groupchat"
            )
            return

        # Fetch active bans once per full sync run instead of once per room.
        # This avoids repeating identical database reads for every room in the batch.
        active_bans = []
        async with self.db.execute("SELECT jid, nick, until, comment FROM bans") as cursor:
            db_bans = await cursor.fetchall()
        for ban_jid, ban_nick, until, comment in db_bans:
            if until > 0 and until <= now:  # skip expired temporary bans
                continue
            active_bans.append((ban_jid, ban_nick, comment))

        async def sync_single_room(
            idx: int,
            room: str,
            active_room_bans: list[tuple[str | None, str | None, str | None]],
        ) -> None:
            # --- Leave and rejoin room to refresh presence ---
            try:
                self.plugin["xep_0045"].leave_muc(room, NICK)
                await asyncio.sleep(0.5)  # short delay
                self.plugin["xep_0045"].join_muc(room, NICK)
                self.room_join_time[room] = time.time()
            except Exception as e:
                log.warning("⚠️ Failed to rejoin room %s: %s", room, e)

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
                await self.bot_send_message(
                    mto=ADMIN_ROOM,
                    mbody=f"⛔ Skipping {room} — bot has no admin/owner rights",
                    mtype="groupchat"
                )
                return

            # --- Announce start of sync ---
            await self.bot_send_message(
                mto=ADMIN_ROOM,
                mbody=f"⏳ Syncing bans in room {room} ({idx}/{total_rooms})...",
                mtype="groupchat"
            )

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

            for ban_jid, ban_nick, comment in active_room_bans:
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
                results = await asyncio.gather(*tasks, return_exceptions=True)
                failed_count = 0
                for result in results:
                    if isinstance(result, Exception):
                        failed_count += 1
                        log.warning("⚠️ Failed to apply ban in %s: %s", room, result)
                if failed_count:
                    await self.bot_send_message(
                        mto=ADMIN_ROOM,
                        mbody=f"⚠️ Failed to apply {failed_count} bans in {room}",
                        mtype="groupchat"
                    )
                log.info(
                    "ℹ️ Applied %d new bans in %s (skipped %d already banned)",
                    new_bans_count - failed_count,
                    room,
                    len(active_room_bans) - new_bans_count,
                )
            else:
                log.info("ℹ️ All bans already applied in %s, nothing to do", room)

            await self.bot_send_message(
                mto=ADMIN_ROOM,
                mbody=f"✅ Finished syncing room {room} ({idx}/{total_rooms}) - {new_bans_count} new bans applied",
                mtype="groupchat"
            )

        # Batch rooms to prevent overwhelming server
        raw_batch_size = getattr(self, "sync_batch_size", 10)
        try:
            batch_size = int(raw_batch_size)
        except (TypeError, ValueError):
            batch_size = 10
        batch_size = max(1, batch_size)
        rooms_list = list(self.protected_rooms)

        for batch_num in range(0, len(rooms_list), batch_size):
            batch = rooms_list[batch_num:batch_num + batch_size]
            batch_start = batch_num + 1
            batch_end = min(batch_num + batch_size, len(rooms_list))

            await self.bot_send_message(
                mto=ADMIN_ROOM,
                mbody=f"⏳ Syncing batch {batch_start}-{batch_end}/{total_rooms}...",
                mtype="groupchat"
            )

            # Run batch in parallel. Keep processing remaining rooms even if one room fails.
            results = await asyncio.gather(*(
                sync_single_room(batch_num + 1 + i, room, active_bans)
                for i, room in enumerate(batch)
            ), return_exceptions=True)
            for room, result in zip(batch, results, strict=False):
                if isinstance(result, Exception):
                    log.warning(
                        "Error syncing room %s in batch %d-%d: %s",
                        room,
                        batch_start,
                        batch_end,
                        result,
                    )
                    await self.bot_send_message(
                        mto=ADMIN_ROOM,
                        mbody=f"⚠️ Error syncing {room} during batch {batch_start}-{batch_end}: {result}",
                        mtype="groupchat"
                    )

            # Small delay between batches to avoid overwhelming server
            if batch_end < len(rooms_list):
                await asyncio.sleep(1)

        log.info("✅ Full %ssync completed for %d rooms", getattr(self, "command_prefix", "!"), total_rooms)
        await self.bot_send_message(
            mto=ADMIN_ROOM,
            mbody=f"✅ Full {getattr(self, 'command_prefix', '!')}sync completed for {total_rooms} rooms in {(len(rooms_list) + batch_size - 1) // batch_size} batches",
            mtype="groupchat"
        )


    async def _sync_outcast_is_expired_tempban(
        self,
        jid_bare: str,
        now: int,
    ) -> bool:
        """
        Return True if this room outcast belongs to an expired JID tempban.

        This prevents sync from promoting an expired tempban that is still set
        as a room outcast into a recovered permanent ban.
        """
        if not jid_bare:
            return False

        target = self.bare_jid(jid_bare)

        async with self.db.execute(
            """
            SELECT until FROM bans
            WHERE target_type = 'jid'
              AND (target = ? OR jid = ?)
              AND until > 0
              AND until <= ?
            LIMIT 1
            """,
            (target, target, now),
        ) as cursor:
            row = await cursor.fetchone()

        return row is not None


    async def _wait_for_bot_admin_rights(
        self,
        room: str,
        timeout: float = 5.0,
        interval: float = 0.5,
    ) -> bool:
        """
        Wait briefly for MUC presence/affiliation state after joining a room.

        This avoids noisy per-ban failures when a room has not yet delivered
        the bot's current admin/owner state.
        """
        deadline = time.time() + timeout

        while time.time() < deadline:
            if self.is_bot_admin_or_owner(room):
                return True

            await asyncio.sleep(interval)

        return self.is_bot_admin_or_owner(room)


    async def sync_bans_to_rooms_for_single_room(self, room: str) -> None:
        """
        Sync bans for a single room (after !room add or !sync).
        Skips expired temporary bans automatically.
        Only applies bans that are NOT already set (outcast affiliation).
        """
        if not await self._wait_for_bot_admin_rights(room):
            log.warning("⛔ Skipping initial sync for %s (bot is not admin/owner)", room)
            await self.bot_send_message(
                mto=ADMIN_ROOM,
                mbody=f"⛔ Cannot sync {room} — bot has no admin/owner rights.",
                mtype="groupchat",
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
            # Important: do not promote expired tempbans that are still present
            # as room outcasts into recovered permanent bans.
            to_insert = []
            for jid_bare in outcasts_bare:
                if any(
                    ban_jid and self.bare_jid(ban_jid) == jid_bare
                    for ban_jid, _, _, _ in active_bans
                ):
                    continue

                if await self._sync_outcast_is_expired_tempban(jid_bare, now):
                    log.info(
                        "♻️ Sync: outcast %s in %s belongs to an expired tempban; unbanning instead of recovering as permanent",
                        jid_bare,
                        room,
                    )
                    await self.unban_all(jid_bare, issuer="system")
                    continue

                to_insert.append((jid_bare, None, 0, issuer_tag, "Recovered from room"))
                active_bans.append((jid_bare, None, 0, "Recovered from room"))

            if to_insert:
                for jid_bare, nick, until_value, issuer_value, comment_value in to_insert:
                    await self.upsert_ban_db(jid_bare, nick, until_value, issuer_value, comment_value)
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
                    tasks.append(
                        self.apply_ban_to_room(
                            room,
                            ban_jid,
                            ban_nick,
                            comment,
                            announce_missing_rights=False,
                        )
                    )
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
            admin_log_list = []

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

                admin_list.append(self.safe_jid(bare))
                admin_log_list.append(bare)

            log.info("Admins synced: %s", ", ".join(admin_log_list))

            if announce:
                if admin_list:
                    msg = "✅ Current admins/owners in Admin-Room:\n" + "\n".join(admin_list)
                else:
                    msg = "⚠️ No admins/owners found in Admin-Room."

                await self.bot_send_message(mto=ADMIN_ROOM, mbody=msg, mtype="groupchat")

        except Exception as e:
            log.warning("Failed to sync admins: %s", e)


    async def sync_bans_to_rooms(self, startup: bool = False, announce_progress: bool = True) -> None:
        """Sync DB bans to rooms while holding the shared ban-state lock."""
        async with ban_state_lock(self):
            await self._sync_bans_to_rooms_locked(startup=startup, announce_progress=announce_progress)

    async def _sync_bans_to_rooms_locked(self, startup: bool = False, announce_progress: bool = True) -> None:
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
                await self.bot_send_message(
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
            await self.bot_send_message(
                mto=ADMIN_ROOM,
                mbody="✅ No active bans to sync.",
                mtype="groupchat"
            )
            return

        # --- Apply bans to each protected room ---
        for idx, room in enumerate(self.protected_rooms, start=1):
            if announce_progress:
                await self.bot_send_message(
                    mto=ADMIN_ROOM,
                    mbody=f"⏳ Syncing bans in room {room} ({idx}/{len(self.protected_rooms)})...",
                    mtype="groupchat"
                )

            # Skip room if bot not admin/owner
            if not await self._wait_for_bot_admin_rights(room, timeout=2.0):
                log.warning("⛔ Skipping %s — bot not admin/owner", room)
                if announce_progress:
                    await self.bot_send_message(
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
            # Important: do not promote expired tempbans that are still present
            # as room outcasts into recovered permanent bans.
            orphan_bans = []
            for jid_bare in outcasts_bare:
                if any(
                    ban_jid and self.bare_jid(ban_jid) == jid_bare
                    for ban_jid, _, _ in active_bans
                ):
                    continue

                if await self._sync_outcast_is_expired_tempban(jid_bare, now):
                    log.info(
                        "♻️ Sync: outcast %s in %s belongs to an expired tempban; unbanning instead of recovering as permanent",
                        jid_bare,
                        room,
                    )
                    await self.unban_all(jid_bare, issuer="system")
                    continue

                orphan_bans.append((jid_bare, None, "Recovered from room"))

            if orphan_bans:
                for jid, nick, comment in orphan_bans:
                    await self.upsert_ban_db(self.bare_jid(jid) if jid else None, nick, 0, "sync_room_add", comment)
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
                    tasks.append(
                        self.apply_ban_to_room(
                            room,
                            ban_jid,
                            ban_nick,
                            comment,
                            announce_missing_rights=False,
                        )
                    )
                    applied_bans_set.add((ban_jid, ban_nick))
                    new_bans_count += 1

            if tasks:
                await asyncio.gather(*tasks)
                log.info("ℹ️ Applied %d new bans in %s (skipped %d already banned)",
                        new_bans_count, room, len(active_bans) - new_bans_count)
            else:
                log.info("ℹ️ All bans already applied in %s, nothing to do", room)

            if announce_progress:
                await self.bot_send_message(
                    mto=ADMIN_ROOM,
                    mbody=f"✅ Finished syncing room {room} ({idx}/{len(self.protected_rooms)}) - {new_bans_count} new bans applied",
                    mtype="groupchat"
                )

        # --- Final statistics ---
        if announce_progress:
            await self.bot_send_message(
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
        announce = (
            getattr(self, "announce_startup", True)
            and getattr(self, "announce_sync_details", True)
        )
        await self.sync_bans_to_rooms(startup=True, announce_progress=announce)


    async def sync_bans(self) -> None:
        await self.sync_bans_to_rooms(startup=False, announce_progress=True)
