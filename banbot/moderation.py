"""Ban, temporary ban, unban, room ban application, and tempban expiry logic."""

import asyncio
import logging
import time

from config import ADMIN_ROOM
from slixmpp.exceptions import IqError, IqTimeout

from .utils import (
    domain_matches,
    human_time,
    looks_like_domain,
    normalize_ban_target,
    validate_domain_ban,
    validate_jid_format,
)

log = logging.getLogger(__name__)


class ModerationMixin:
    async def apply_ban_to_room(
        self,
        room: str,
        ban_jid: str | None,
        ban_nick: str | None,
        comment: str | None,
        issuer: str | None = None,
        announce_missing_rights: bool = True,
    ) -> None:
        """
        Apply a ban to a room:
        - Supports JID, Nick, or Domain (*.domain.tld)
        - Sets outcast (works offline)
        - Kicks matching occupants in parallel
        - Sends notifications:
            - Admin room: full info (JID + Nick)
            - Protected rooms: only nick (anonymized)
        """
        # --- Safety: Do nothing if bot has no admin rights ---
        if not self.is_bot_admin_or_owner(room):
            log.warning("⛔ Cannot apply ban in %s (bot not admin/owner)", room)

            if announce_missing_rights:
                await self.bot_send_message(
                    mto=ADMIN_ROOM,
                    mbody=f"⛔ Cannot apply ban in {room} — missing admin/owner rights.",
                    mtype="groupchat",
                )

            return

        is_domain = ban_jid and ban_jid.startswith("*.")
        ban_jid_bare = None if is_domain else self.bare_jid(ban_jid)
        room_occupants = self.occupants.get(room, {})

        # --- Step 1: Set Outcast (offline ban) ---
        if ban_jid_bare and not is_domain:
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
        async def kick_nick(nick_name: str, info: dict) -> None:
            """Inner function to kick a single user."""
            jid_in_room = info.get("jid")
            match = False

            if is_domain and jid_in_room:
                domain = self.bare_jid(jid_in_room).split("@")[1].lower()
                match = domain_matches(domain, ban_jid[2:])
            elif ban_jid_bare and jid_in_room:
                match = self.bare_jid(jid_in_room) == ban_jid_bare
            elif ban_nick:
                match = nick_name.lower() == ban_nick.lower()

            if match:
                # Skip admins/owners
                if info.get("affiliation") in ("owner", "admin"):
                    log.info("❌ Skipped kick for admin/owner %s in %s", nick_name, room)
                    return

                for attempt in range(3):
                    try:
                        async with self.muc_write_semaphore:
                            await self.plugin["xep_0045"].set_role(
                                room=room,
                                nick=nick_name,
                                role="none",
                                reason=comment or "Banned by admin"
                            )
                        log.info("✅ Kicked %s from %s", nick_name, room)
                        break
                    except IqTimeout:
                        log.warning("Timeout kicking %s in %s, retrying...", nick_name, room)
                        await asyncio.sleep(1)
                    except IqError as e:
                        log.warning("IqError kicking %s in %s: %s", nick_name, room, e)
                        break

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
            except IqError as e:
                log.debug("Could not kick nick-only user %s: %s", ban_nick, e)
            except IqTimeout:
                log.warning("Timeout kicking nick-only user %s", ban_nick)

        # --- Step 4: Notifications ---
        if room == ADMIN_ROOM:
            display = self.bare_jid(ban_jid) if ban_jid else (ban_nick or "Unknown")
            msg_admin = f"✅ Banned {display}" + (f" ({comment})" if comment else "") + f" by {issuer}"
            await self.bot_send_message(mto=ADMIN_ROOM, mbody=msg_admin, mtype="groupchat")
        elif room in self.protected_rooms:
            if self.allow_user_cmds and self.show_ban_in_muc:
                if not ban_nick:
                    log.debug(
                        "Skipping public ban announcement in %s because no public nick is known",
                        room,
                    )
                    return

                msg = f"✅ Banned {ban_nick}" + (f" ({comment})" if comment else "")
                await self.bot_send_message(mto=room, mbody=msg, mtype="groupchat")


    async def ban_all(self, identifier: str, until: int | None, issuer: str, comment: str | None = None) -> None:
        """
        Bans a user by JID, nick, or domain (*.domain.tld):
        - Validates JID/domain format
        - Checks tempban duration limits
        - Handles duplicate bans through target_type/target
        - Protects admins/owners using local occupant cache and server affiliations
        """
        ts = until if until is not None else 0
        identifier = identifier.strip().lower()

        ban_jid = None
        ban_nick = None
        is_domain = identifier.startswith("*.")

        if looks_like_domain(identifier):
            await self.bot_send_message(
                mto=ADMIN_ROOM,
                mbody=(
                    f"❌ Invalid domain ban: {identifier}\n"
                    f"Use wildcard format instead: *.{identifier}"
                ),
                mtype="groupchat"
            )
            return

        if is_domain:
            is_valid, error_msg = validate_domain_ban(identifier)
            if not is_valid:
                await self.bot_send_message(mto=ADMIN_ROOM, mbody=error_msg, mtype="groupchat")
                return
            ban_jid = identifier
        else:
            is_jid = "@" in identifier
            if is_jid and not validate_jid_format(identifier):
                await self.bot_send_message(
                    mto=ADMIN_ROOM,
                    mbody=f"❌ Invalid JID format: {identifier}. Expected: user@domain.tld",
                    mtype="groupchat"
                )
                return

            ban_jid = identifier if is_jid else None
            ban_nick = None if is_jid else identifier.lower()

            if ban_nick and not ban_jid:
                for room_occ in list(self.occupants.values()):
                    for n, info in list(room_occ.items()):
                        if n.lower() == ban_nick and info.get("jid"):
                            ban_jid = self.bare_jid(info["jid"])
                            break
                    if ban_jid:
                        break

            ban_jid_bare = self.bare_jid(ban_jid) if ban_jid else None
            if ban_jid and not ban_nick:
                for room_occ in list(self.occupants.values()):
                    for n, info in list(room_occ.items()):
                        if info.get("jid") and self.bare_jid(info["jid"]) == ban_jid_bare:
                            ban_nick = n.lower()
                            break
                    if ban_nick:
                        break

        if until is not None and until > 0:
            max_days = self.max_tempban_days
            max_seconds = max_days * 86400
            duration = until - int(time.time())

            if duration <= 0:
                await self.bot_send_message(
                    mto=ADMIN_ROOM,
                    mbody="❌ Invalid duration: must be in the future.",
                    mtype="groupchat"
                )
                return

            if duration > max_seconds:
                await self.bot_send_message(
                    mto=ADMIN_ROOM,
                    mbody=f"❌ Tempban duration exceeds MAX_TEMPBAN_DAYS ({max_days} days). Max: {max_days} days.",
                    mtype="groupchat"
                )
                return

        target_type, target, normalized_jid, normalized_nick = normalize_ban_target(ban_jid, ban_nick)
        if target_type == "domain" and normalized_jid is None:
            normalized_jid = f"*.{target}"

        # If a nick-only command targets a nick that is already known on an
        # active JID ban, update that JID ban instead of creating a second
        # independent nick-only ban. This avoids duplicate tempbans for the
        # same person when they temporarily leave all rooms.
        if target_type == "nick" and normalized_nick:
            existing_jid_ban = await self.find_active_jid_ban_by_nick(normalized_nick)
            if existing_jid_ban:
                existing_jid, _existing_until, _existing_issuer, _existing_comment = existing_jid_ban
                log.info(
                    "🔗 Resolving nick-only ban %s to existing JID ban %s",
                    normalized_nick,
                    existing_jid,
                )
                normalized_jid = existing_jid
                target_type = "jid"
                target = existing_jid
                ban_jid = existing_jid

        db_key = f"*.{target}" if target_type == "domain" else target
        skip_final_message = False

        if db_key in self.ban_cache:
            existing_jid, existing_nick, existing_until, existing_issuer, existing_comment = self.ban_cache[db_key]
            existing_is_permanent = existing_until <= 0
            new_is_permanent = ts <= 0

            if existing_is_permanent and new_is_permanent:
                await self.bot_send_message(
                    mto=ADMIN_ROOM,
                    mbody=f"ℹ️ Ban already exists for {identifier} (permanent)",
                    mtype="groupchat"
                )
                self.log_event(logging.INFO, "ban_duplicate_ignored", actor=issuer, identifier=identifier, target_type=target_type, target=target)
                await self.audit_event("ban_duplicate_ignored", actor=issuer, target_type=target_type, target=target, jid=normalized_jid, nick=normalized_nick, comment=comment)
                return
            elif existing_is_permanent and not new_is_permanent:
                log.info("🔄 Converting permanent ban to tempban for %s", identifier)
                await self.bot_send_message(
                    mto=ADMIN_ROOM,
                    mbody=f"🔄 Converting permanent ban to tempban for {identifier} ({human_time(until - int(time.time()))})",
                    mtype="groupchat"
                )
                skip_final_message = True
            elif not existing_is_permanent and new_is_permanent:
                log.info("🔄 Converting tempban to permanent ban for %s", identifier)
                await self.bot_send_message(
                    mto=ADMIN_ROOM,
                    mbody=f"🔄 Converting tempban to permanent ban for {identifier}",
                    mtype="groupchat"
                )
                skip_final_message = True
            else:
                new_duration = human_time(until - int(time.time()))
                old_duration = human_time(max(0, existing_until - int(time.time())))
                log.info("🔄 Updating tempban for %s: %s → %s", identifier, old_duration, new_duration)
                await self.bot_send_message(
                    mto=ADMIN_ROOM,
                    mbody=f"🔄 Ban updated: {identifier}'s tempban duration changed from {old_duration} to {new_duration}",
                    mtype="groupchat"
                )
                skip_final_message = True

        protected, reason = await self.is_protected_admin_target(
            identifier,
            nick=ban_nick,
            jid=normalized_jid,
        )
        if protected:
            await self.bot_send_message(
                mto=ADMIN_ROOM,
                mbody=f"❌ Refusing ban: {reason}",
                mtype="groupchat",
            )
            self.log_event(logging.WARNING, "ban_refused_admin_protected", actor=issuer, identifier=identifier, target_type=target_type, target=target, reason=reason)
            await self.audit_event("ban_refused_admin_protected", actor=issuer, target_type=target_type, target=target, jid=normalized_jid, nick=normalized_nick, comment=comment, details={"reason": reason, "identifier": identifier})
            return

        # --- Ignorelist protection ---
        ignore_candidate = normalized_jid or target or identifier

        if self.is_ignored_target(ignore_candidate):
            await self.bot_send_message(
                mto=ADMIN_ROOM,
                mbody=f"⛔ Refusing ban: {ignore_candidate} is on the ignorelist.",
                mtype="groupchat",
            )
            self.log_event(
                logging.WARNING, "ban_refused_ignorelist",
                actor=issuer, identifier=identifier,
                target_type=target_type, target=target,
            )
            await self.audit_event(
                "ban_refused_ignorelist", actor=issuer,
                target_type=target_type, target=target,
                jid=normalized_jid, nick=normalized_nick,
            )
            return

        try:
            await self.upsert_ban_db(normalized_jid, normalized_nick, ts, issuer, comment)
        except Exception as e:
            log.error("Database error when saving ban: %s", e)
            await self.bot_send_message(
                mto=ADMIN_ROOM,
                mbody=f"❌ Database error: {e}",
                mtype="groupchat"
            )
            return

        # --- RTBL Publish: Update your own outbound ban feed ---
        # Publish only permanent JID/domain bans. Nick-only bans and tempbans
        # are not suitable for RTBL. If an existing permanent ban is converted
        # to a tempban, retract it from the publish feed.
        if target_type == "jid" and normalized_jid and not normalized_jid.startswith("*."):
            if ts <= 0:
                await self.rtbl_publish_ban(jid=normalized_jid, domain=None, comment=comment)
            else:
                await self.rtbl_retract_ban(jid=normalized_jid, domain=None)
        elif target_type == "domain" and target:
            if ts <= 0:
                await self.rtbl_publish_ban(jid=None, domain=target, comment=comment)
            else:
                await self.rtbl_retract_ban(jid=None, domain=target)

        event_type = "ban_updated" if skip_final_message else "ban_applied"
        log.info("Ban applied: identifier=%s, JID/Nick=%s/%s, until=%s, issuer=%s",
                 identifier, normalized_jid, normalized_nick, ts, issuer)
        self.log_event(logging.INFO, event_type, actor=issuer, identifier=identifier, target_type=target_type, target=target, jid=normalized_jid, nick=normalized_nick, until=ts, comment=comment)
        await self.audit_event(event_type, actor=issuer, target_type=target_type, target=target, jid=normalized_jid, nick=normalized_nick, until=ts, comment=comment, details={"identifier": identifier})

        if hasattr(self, "maybe_auto_redact_after_ban") and normalized_jid and target_type == "jid":
            await self.maybe_auto_redact_after_ban(normalized_jid, comment, actor=issuer)

        if not skip_final_message:
            display = normalized_jid if normalized_jid else (normalized_nick or "Unknown")
            time_info = f" ({human_time(ts - int(time.time()))})" if ts > 0 else ""
            msg_admin = f"✅ Banned {display}{time_info}" + (f" ({comment})" if comment else "") + f" by {issuer}"
            await self.bot_send_message(mto=ADMIN_ROOM, mbody=msg_admin, mtype="groupchat")

        for room in self.protected_rooms:
            try:
                if is_domain:
                    for n, info in list(self.occupants.get(room, {}).items()):
                        jid_in_room = info.get("jid")
                        bare_in_room = self.bare_jid(jid_in_room) if jid_in_room else None
                        domain_in_room = bare_in_room.split("@", 1)[1].lower() if bare_in_room and "@" in bare_in_room else None
                        if domain_matches(domain_in_room, target):
                            await self.apply_ban_to_room(room, bare_in_room, n, comment, issuer)
                else:
                    await self.apply_ban_to_room(room, normalized_jid, normalized_nick, comment, issuer)
            except (IqError, IqTimeout) as e:
                log.warning("Failed to ban/kick %s in %s: %s", identifier, room, e)


    async def unban_worker(self) -> None:
        """
        Periodically unban users whose temporary bans have expired.
        Runs in an infinite loop every 60 seconds (configurable via UNBAN_CHECK_INTERVAL).
        Improved error handling to prevent crashes.
        """
        while True:
            now = int(time.time())
            try:
                # --- Fetch expired bans (limited to 100 per check) ---
                async with self.db.execute(
                    "SELECT target_type, target, jid, nick FROM bans WHERE until > 0 AND until <= ? LIMIT 100", (now,)
                ) as cursor:
                    rows = await cursor.fetchall()

                expired = []
                for target_type, target, ban_jid, ban_nick in rows:
                    identifier = f"*.{target}" if target_type == "domain" else (self.bare_jid(ban_jid) if ban_jid else ban_nick)
                    log.info("⏳ Temporary ban expired: %s, auto-unbanning...", identifier)
                    expired.append(identifier)
                    await self.unban_all(identifier, issuer="system")

                if expired:
                    await self.load_bans_from_db()
                    log.info("✅ Auto-unbanned %d users", len(expired))
                    self.log_event(logging.INFO, "tempbans_expired", count=len(expired), identifiers=expired)

                await self.cleanup_old_audit_logs()

                # Check if there are more expired bans pending
                async with self.db.execute(
                    "SELECT COUNT(*) FROM bans WHERE until > 0 AND until <= ?", (now,)
                ) as cursor:
                    count_row = await cursor.fetchone()
                    if count_row and count_row[0] > 0:
                        log.debug("ℹ️ %d more expired bans pending for next cycle", count_row[0])

            except asyncio.CancelledError:
                log.info("unban_worker cancelled")
                raise
            except Exception as e:
                log.warning("Error in unban_worker: %s", e)
                await asyncio.sleep(5)
                continue

            # Configurable check interval (reloadable via !reloadconfig)
            check_interval = self.unban_check_interval
            await asyncio.sleep(check_interval)


    async def apply_unban_to_room(
        self,
        room: str,
        ban_jid: str | None,
        ban_nick: str | None,
        domain: str | None = None
    ) -> None:
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
                    except IqError as e:
                        log.debug("IqError removing outcast for %s in %s: %s", bare, room, e)
                        break

            # --- Step 2: Restore role if online ---
            room_occupants = self.occupants.get(room, {})
            for n, info in room_occupants.items():
                jid_in_room = info.get("jid")
                if ((ban_jid and jid_in_room and self.bare_jid(jid_in_room) == self.bare_jid(ban_jid)) or
                    (ban_nick and n.lower() == ban_nick) or
                    (domain and jid_in_room and domain_matches(self.bare_jid(jid_in_room).split("@")[1].lower(), domain))):
                    for attempt in range(2):
                        try:
                            async with self.muc_write_semaphore:
                                await self.plugin["xep_0045"].set_role(
                                    room=room,
                                    nick=n,
                                    role="participant"
                                )
                            log.info("✅ Participant role restored for %s in %s", n, room)
                            break
                        except IqTimeout:
                            log.warning("Timeout restoring role for %s in %s, retrying...", n, room)
                            await asyncio.sleep(1)
                        except IqError as e:
                            log.debug("IqError restoring role for %s in %s: %s", n, room, e)
                            break

            # --- Step 3: Notifications ---
            if room == ADMIN_ROOM:
                display_admin = ban_jid or ban_nick or "Unknown"
                msg_admin = f"♻️ Unbanned {display_admin}"
                await self.bot_send_message(mto=ADMIN_ROOM, mbody=msg_admin, mtype="groupchat")

            elif self.allow_user_cmds:
                if not ban_nick:
                    log.debug(
                        "Skipping public unban announcement in %s because no public nick is known",
                        room,
                    )
                    return

                msg = f"♻️ Unbanned {ban_nick}"
                await self.notify_protected(room, msg)

        except (IqError, IqTimeout) as e:
            log.warning("Failed to unban %s in %s: %s", ban_jid or ban_nick, room, e)


    async def unban_all(self, identifier: str, issuer: str | None = None) -> None:
        """
        Remove a ban from a user (JID, nick, or domain) and unban in all protected rooms.
        Supports exact wildcard-domain unbans (*.domain.tld).
        """
        if not identifier:
            return

        identifier = identifier.strip().lower()

        if looks_like_domain(identifier):
            await self.bot_send_message(
                mto=ADMIN_ROOM,
                mbody=(
                    f"❌ Invalid domain unban: {identifier}\n"
                    f"Use wildcard format instead: *.{identifier}"
                ),
                mtype="groupchat"
            )
            return

        is_domain_ban = identifier.startswith("*.")
        is_jid = "@" in identifier
        domain = identifier[2:].strip(".") if is_domain_ban else None

        if is_domain_ban:
            is_valid, error_msg = validate_domain_ban(identifier)
            if not is_valid:
                await self.bot_send_message(mto=ADMIN_ROOM, mbody=error_msg, mtype="groupchat")
                return
            target_type = "domain"
            target = domain
        elif is_jid:
            target_type = "jid"
            target = self.bare_jid(identifier)
        else:
            target_type = "nick"
            target = identifier

        row = None
        async with self.db.execute(
            "SELECT jid, nick, until, issuer FROM bans WHERE target_type = ? AND target = ?",
            (target_type, target),
        ) as cur:
            row = await cur.fetchone()

        if not row and not is_domain_ban and not is_jid:
            async with self.db.execute("SELECT jid, nick, until, issuer FROM bans WHERE target_type = 'jid'") as cursor:
                async for jid_db, nick_db, until_db, issuer_db in cursor:
                    if jid_db and self.bare_jid(jid_db).split("@", 1)[0].lower() == identifier:
                        row = (jid_db, nick_db, until_db, issuer_db)
                        target_type = "jid"
                        target = self.bare_jid(jid_db)
                        break

        if not row:
            await self.bot_send_message(
                mto=ADMIN_ROOM,
                mbody=f"❌ No ban found for {identifier}",
                mtype="groupchat"
            )
            return

        ban_jid = row[0] if row and row[0] else None
        ban_nick = row[1] if row and row[1] else None
        ban_until = int(row[2] or 0) if row else 0
        ban_issuer = (row[3] or "") if row and len(row) > 3 else ""

        await self.db.execute(
            "DELETE FROM bans WHERE target_type = ? AND target = ?",
            (target_type, target),
        )
        await self.db.commit()

        # --- RTBL Publish: Withdraw permanent bans from own feed ---
        # Only permanent JID/domain bans are published, so only those need to
        # be retracted. Tempban expiry/unban should not touch the publish feed.
        if ban_until <= 0 and ban_issuer != "rtbl":
            if target_type == "jid" and ban_jid and not (ban_jid or "").startswith("*."):
                await self.rtbl_retract_ban(jid=ban_jid, domain=None)
            elif target_type == "domain" and domain:
                await self.rtbl_retract_ban(jid=None, domain=domain)

        if is_domain_ban and domain:
            self._remove_domain_bans_from_cache(domain)
        else:
            self._remove_ban_from_cache(identifier, ban_jid=ban_jid, ban_nick=ban_nick)

        for room in self.protected_rooms:
            try:
                await self.apply_unban_to_room(
                    room,
                    ban_jid if not is_domain_ban else None,
                    ban_nick,
                    domain=domain if is_domain_ban else None
                )
            except Exception as e:
                log.warning("Error unbanning %s in %s: %s", identifier, room, e)

        if issuer == "system":
            msg_admin = f"♻️ Unbanned {identifier} (tempban expired)"
        else:
            msg_admin = f"♻️ Unbanned {identifier}" + (f" by {issuer}" if issuer else "")
        await self.bot_send_message(mto=ADMIN_ROOM, mbody=msg_admin, mtype="groupchat")
        log.info(msg_admin)
        event_type = "tempban_expired" if issuer == "system" else "unban_applied"
        self.log_event(logging.INFO, event_type, actor=issuer or "system", identifier=identifier, target_type=target_type, target=target, jid=ban_jid, nick=ban_nick)
        await self.audit_event(event_type, actor=issuer or "system", target_type=target_type, target=target, jid=ban_jid, nick=ban_nick, details={"identifier": identifier})
