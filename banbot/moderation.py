"""Ban, temporary ban, unban, room ban application, and tempban expiry logic."""

import asyncio
import logging
import time

from slixmpp.exceptions import IqError, IqTimeout

from config import ADMIN_ROOM

from .locks import ban_state_lock, is_maintenance_mode
from .utils import (
    domain_matches,
    human_time,
    looks_like_domain,
    normalize_actor,
    normalize_ban_target,
    validate_domain_ban,
    validate_jid_format,
)

log = logging.getLogger(__name__)


class ModerationMixin:
    def _auto_redaction_matches_ban_reason(self, comment: str | None) -> bool:
        """Return whether a command ban will actually start auto-redaction.

        Production BanBot provides the shared reason matcher through
        RedactionMixin. Lightweight mixin users without that helper retain the
        previous behavior and may decide inside maybe_auto_redact_after_ban().
        """
        matcher = getattr(self, "_redaction_auto_reason_matches", None)
        if not callable(matcher):
            return True
        return bool(matcher(comment))

    async def _announce_auto_redaction_started(self, jid: str) -> None:
        """Tell administrators that slow redaction work is now backgrounded.

        A notification failure must never prevent the actual cleanup task.
        """
        try:
            await self.bot_send_message(
                mto=ADMIN_ROOM,
                mbody=(
                    f"🧹 Auto-redaction started in the background for {jid}. "
                    "Results will follow."
                ),
                mtype="groupchat",
            )
        except Exception:
            log.exception(
                "Could not announce automatic redaction start for %s",
                jid,
            )

    def _schedule_auto_redaction_after_ban(
        self,
        jid: str,
        comment: str | None,
        actor: str | None,
    ) -> None:
        """Run potentially slow bulk redaction outside the ban command path."""
        task = asyncio.create_task(
            self.maybe_auto_redact_after_ban(jid, comment, actor=actor),
            name=f"auto-redact:{jid}",
        )
        tasks = getattr(self, "redaction_operation_tasks", None)
        if tasks is None:
            tasks = set()
            self.redaction_operation_tasks = tasks
        tasks.add(task)

        def _done(completed: asyncio.Task) -> None:
            tasks.discard(completed)
            if completed.cancelled():
                return
            try:
                completed.result()
            except Exception:
                log.exception("Automatic redaction failed for %s", jid)

        task.add_done_callback(_done)


    async def _apply_ban_to_protected_rooms(
        self,
        *,
        identifier: str,
        is_domain: bool,
        target: str,
        normalized_jid: str | None,
        normalized_nick: str | None,
        comment: str | None,
        issuer: str,
    ) -> None:
        """Apply one committed ban to all protected rooms concurrently."""

        async def apply_to_room(room: str) -> None:
            try:
                if is_domain:
                    for nick, info in list(self.occupants.get(room, {}).items()):
                        jid_in_room = info.get("jid")
                        bare_in_room = self.bare_jid(jid_in_room) if jid_in_room else None
                        domain_in_room = (
                            bare_in_room.split("@", 1)[1].lower()
                            if bare_in_room and "@" in bare_in_room
                            else None
                        )
                        if domain_matches(domain_in_room, target):
                            await self.apply_ban_to_room(
                                room,
                                bare_in_room,
                                nick,
                                comment,
                                issuer,
                            )
                else:
                    await self.apply_ban_to_room(
                        room,
                        normalized_jid,
                        normalized_nick,
                        comment,
                        issuer,
                    )
            except Exception as exc:
                log.warning(
                    "Failed to ban/kick %s in %s: %s",
                    identifier,
                    room,
                    exc,
                )

        await asyncio.gather(
            *(apply_to_room(room) for room in sorted(self.protected_rooms))
        )


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
            for _attempt in range(3):
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

                for _attempt in range(3):
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
            issuer_display = normalize_actor(issuer) or "unknown"
            msg_admin = f"✅ Banned {display}" + (f" ({comment})" if comment else "") + f" by {issuer_display}"
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


    async def ban_all(
        self,
        identifier: str,
        until: int | None,
        issuer: str,
        comment: str | None = None,
        *,
        auto_redact: bool = True,
        notify_policy: bool = True,
    ) -> None:
        """Ban a target while holding the shared ban-state lock."""
        issuer = normalize_actor(issuer) or "unknown"
        async with ban_state_lock(self):
            await self._ban_all_locked(
                identifier,
                until,
                issuer,
                comment,
                auto_redact=auto_redact,
                notify_policy=notify_policy,
            )

    async def _ban_all_locked(
        self,
        identifier: str,
        until: int | None,
        issuer: str,
        comment: str | None = None,
        *,
        auto_redact: bool = True,
        notify_policy: bool = True,
    ) -> None:
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

        db_key = f"*.{target}" if target_type == "domain" else target
        skip_final_message = False
        update_details: dict[str, object] = {"identifier": identifier}
        db_issuer = issuer

        if db_key in self.ban_cache:
            existing_jid, existing_nick, existing_until, existing_issuer, existing_comment = self.ban_cache[db_key]
            existing_is_permanent = existing_until <= 0
            new_is_permanent = ts <= 0

            # Re-running a moderation command without a comment must not erase
            # the existing reason. A supplied comment is an explicit reason
            # update, including while changing a tempban duration/type.
            effective_comment = comment if comment is not None else existing_comment
            reason_changed = comment is not None and comment != existing_comment
            update_details.update(
                {
                    "old_until": existing_until,
                    "new_until": ts,
                    "old_comment": existing_comment,
                    "new_comment": effective_comment,
                }
            )
            comment = effective_comment

            if existing_is_permanent and new_is_permanent:
                if not reason_changed:
                    await self.bot_send_message(
                        mto=ADMIN_ROOM,
                        mbody=f"ℹ️ Ban already exists for {identifier} (permanent)",
                        mtype="groupchat"
                    )
                    self.log_event(logging.INFO, "ban_duplicate_ignored", actor=issuer, identifier=identifier, target_type=target_type, target=target)
                    await self.audit_event(
                        "ban_duplicate_ignored",
                        actor=issuer,
                        target_type=target_type,
                        target=target,
                        jid=normalized_jid,
                        nick=normalized_nick,
                        comment=comment,
                    )
                    return

                # A reason-only update keeps the original ban issuer and its
                # permanent status. The current actor is recorded in audit.
                db_issuer = existing_issuer
                log.info("🔄 Updating permanent ban reason for %s", identifier)
                await self.bot_send_message(
                    mto=ADMIN_ROOM,
                    mbody=f"🔄 Ban reason updated for {identifier}: {existing_comment or '—'} → {comment or '—'}",
                    mtype="groupchat",
                )
                skip_final_message = True
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
                reason_suffix = " and reason" if reason_changed else ""
                await self.bot_send_message(
                    mto=ADMIN_ROOM,
                    mbody=(
                        f"🔄 Ban updated: {identifier}'s tempban duration changed "
                        f"from {old_duration} to {new_duration}{reason_suffix}"
                    ),
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
            await self.upsert_ban_db(normalized_jid, normalized_nick, ts, db_issuer, comment)
        except Exception as e:
            log.error("Database error when saving ban: %s", e)
            await self.bot_send_message(
                mto=ADMIN_ROOM,
                mbody=f"❌ Database error: {e}",
                mtype="groupchat"
            )
            return

        event_type = "ban_updated" if skip_final_message else "ban_applied"
        log.info("Ban applied: identifier=%s, JID/Nick=%s/%s, until=%s, issuer=%s",
                 identifier, normalized_jid, normalized_nick, ts, issuer)
        self.log_event(logging.INFO, event_type, actor=issuer, identifier=identifier, target_type=target_type, target=target, jid=normalized_jid, nick=normalized_nick, until=ts, comment=comment)
        await self.audit_event(event_type, actor=issuer, target_type=target_type, target=target, jid=normalized_jid, nick=normalized_nick, until=ts, comment=comment, details=update_details)

        if notify_policy and hasattr(self, "notify_policy_change"):
            await self.notify_policy_change(
                event_type,
                actor=issuer,
                target=normalized_jid or normalized_nick or target,
                comment=comment,
            )

        # The local ban is committed at this point. Acknowledge it before any
        # potentially slow room, RTBL, or redaction network operations.
        if not skip_final_message:
            display = normalized_jid if normalized_jid else (normalized_nick or "Unknown")
            time_info = f" ({human_time(ts - int(time.time()))})" if ts > 0 else ""
            issuer_display = normalize_actor(issuer) or "unknown"
            msg_admin = (
                f"✅ Banned {display}{time_info}"
                + (f" ({comment})" if comment else "")
                + f" by {issuer_display}"
            )
            await self.bot_send_message(
                mto=ADMIN_ROOM,
                mbody=msg_admin,
                mtype="groupchat",
            )

        # Prioritize the actual room ban over cleanup and publication work.
        # The semaphore inside apply_ban_to_room still limits XMPP writes.
        await self._apply_ban_to_protected_rooms(
            identifier=identifier,
            is_domain=is_domain,
            target=target,
            normalized_jid=normalized_jid,
            normalized_nick=normalized_nick,
            comment=comment,
            issuer=issuer,
        )

        # Command-level auto-redaction is intentionally limited to permanent
        # JID bans. It is scheduled only after room enforcement, so bulk IQ/MAM
        # traffic cannot delay the ban acknowledgement or the outcast writes.
        if (
            auto_redact
            and ts <= 0
            and hasattr(self, "maybe_auto_redact_after_ban")
            and normalized_jid
            and target_type == "jid"
            and self._auto_redaction_matches_ban_reason(comment)
        ):
            await self._announce_auto_redaction_started(normalized_jid)
            self._schedule_auto_redaction_after_ban(normalized_jid, comment, issuer)

        # Mirror the already committed ban into the optional outbound RTBL.
        # This happens after acknowledgement and room enforcement, so PubSub
        # delays do not affect the visible moderation response.
        if target_type == "jid" and normalized_jid and not normalized_jid.startswith("*."):
            if ts <= 0:
                await self.rtbl_publish_ban(
                    jid=normalized_jid,
                    domain=None,
                    comment=comment,
                )
            else:
                await self.rtbl_retract_ban(jid=normalized_jid, domain=None)
        elif target_type == "domain" and target:
            if ts <= 0:
                await self.rtbl_publish_ban(
                    jid=None,
                    domain=target,
                    comment=comment,
                )
            else:
                await self.rtbl_retract_ban(jid=None, domain=target)


    async def unban_worker(self) -> None:
        """
        Periodically unban users whose temporary bans have expired.
        Runs in an infinite loop every 60 seconds (configurable via UNBAN_CHECK_INTERVAL).
        Improved error handling to prevent crashes.
        """
        while True:
            now = int(time.time())
            try:
                if is_maintenance_mode(self):
                    log.debug("unban_worker skipped while maintenance operation is active")
                    await asyncio.sleep(self.unban_check_interval)
                    continue
                if getattr(self, "reconnecting", False):
                    # Keep the expired DB row until XMPP is usable again. The
                    # row is the evidence sync needs to recognize a still-set
                    # MUC outcast as an expired tempban instead of recovering it
                    # as a permanent manual ban.
                    log.debug("unban_worker skipped while reconnecting")
                    await asyncio.sleep(self.unban_check_interval)
                    continue
                # --- Fetch expired bans (limited to 100 per check) ---
                async with self.db.execute(
                    "SELECT target_type, target, jid, nick FROM bans WHERE until > 0 AND until <= ? LIMIT 100", (now,)
                ) as cursor:
                    rows = await cursor.fetchall()

                expired = []
                for target_type, target, ban_jid, ban_nick in rows:
                    identifier = f"*.{target}" if target_type == "domain" else (self.bare_jid(ban_jid) if ban_jid else ban_nick)
                    log.info("⏳ Temporary ban expired: %s, auto-unbanning...", identifier)
                    removed = await self.unban_all(
                        identifier,
                        issuer="system",
                        notify_policy=False,
                    )
                    if removed:
                        expired.append(identifier)

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
        domain: str | None = None,
        *,
        announce: bool = True,
    ) -> bool:
        """Remove a room outcast and return whether server-side removal succeeded."""
        try:
            # --- Step 1: Remove Outcast (works for offline occupants) ---
            # A MUC affiliation list may contain domainpart-only outcasts such
            # as ``xmpp.party``. Those are represented in BanBot as wildcard
            # domain bans, while the server-side affiliation key is the bare
            # domain and must be cleared explicitly.
            outcast_target = domain or (self.bare_jid(ban_jid) if ban_jid else None)
            if outcast_target:
                bare = outcast_target
                removed = False
                for attempt in range(3):
                    try:
                        async with self.muc_write_semaphore:
                            await self.plugin["xep_0045"].set_affiliation(
                                room=room,
                                jid=bare,
                                affiliation="none",
                            )
                        log.info("✅ Outcast removed for %s in %s", bare, room)
                        removed = True
                        break
                    except IqTimeout:
                        log.warning("Timeout removing outcast for %s in %s, retrying...", bare, room)
                        if attempt < 2:
                            await asyncio.sleep(1)
                    except IqError as exc:
                        log.debug("IqError removing outcast for %s in %s: %s", bare, room, exc)
                        break
                if not removed:
                    return False

            # --- Step 2: Restore role if online ---
            room_occupants = self.occupants.get(room, {})
            nick_role_restore_failed = False
            for nick, info in room_occupants.items():
                jid_in_room = info.get("jid")
                jid_bare = self.bare_jid(jid_in_room) if jid_in_room else ""
                jid_domain = jid_bare.partition("@")[2].lower()
                matches_domain = bool(domain and jid_domain and domain_matches(jid_domain, domain))
                if (
                    (ban_jid and jid_in_room and jid_bare == self.bare_jid(ban_jid))
                    or (ban_nick and nick.lower() == ban_nick)
                    or matches_domain
                ):
                    restored = False
                    for attempt in range(2):
                        try:
                            async with self.muc_write_semaphore:
                                await self.plugin["xep_0045"].set_role(
                                    room=room,
                                    nick=nick,
                                    role="participant",
                                )
                            log.info("✅ Participant role restored for %s in %s", nick, room)
                            restored = True
                            break
                        except IqTimeout:
                            log.warning("Timeout restoring role for %s in %s, retrying...", nick, room)
                            if attempt < 1:
                                await asyncio.sleep(1)
                        except IqError as exc:
                            log.debug("IqError restoring role for %s in %s: %s", nick, room, exc)
                            break
                    # Nick-only bans have no persistent affiliation to clear;
                    # restoring the matching occupant role is the actual
                    # server-side unban operation, so report its failure.
                    if ban_nick and not outcast_target and not restored:
                        nick_role_restore_failed = True

            if nick_role_restore_failed:
                return False

            # --- Step 3: Optional public notification ---
            if announce and room != ADMIN_ROOM and self.allow_user_cmds:
                if not ban_nick:
                    log.debug(
                        "Skipping public unban announcement in %s because no public nick is known",
                        room,
                    )
                else:
                    await self.notify_protected(room, f"♻️ Unbanned {ban_nick}")

            return True
        except (IqError, IqTimeout) as exc:
            log.warning("Failed to unban %s in %s: %s", ban_jid or ban_nick or domain, room, exc)
            return False


    async def unban_all(
        self,
        identifier: str,
        issuer: str | None = None,
        *,
        notify_policy: bool = True,
    ) -> bool:
        """Unban a target while holding the shared ban-state lock."""
        issuer = normalize_actor(issuer)
        async with ban_state_lock(self):
            return await self._unban_all_locked(
                identifier,
                issuer=issuer,
                notify_policy=notify_policy,
            )

    async def _unban_all_locked(
        self,
        identifier: str,
        issuer: str | None = None,
        *,
        notify_policy: bool = True,
    ) -> bool:
        """
        Remove a ban from a user (JID, nick, or domain) and unban in all protected rooms.
        Supports exact domain unbans in both domain.tld and *.domain.tld form.
        """
        if not identifier:
            return False

        identifier = identifier.strip().lower()

        async def fetch_exact(target_type: str, target: str):
            async with self.db.execute(
                "SELECT jid, nick, until, issuer "
                "FROM bans WHERE target_type = ? AND target = ?",
                (target_type, target),
            ) as cursor:
                return await cursor.fetchone()

        row = None
        target_type = ""
        target = ""
        domain: str | None = None
        is_jid = "@" in identifier

        if identifier.startswith("*."):
            domain = identifier[2:].strip(".")
            wildcard_identifier = f"*.{domain}"
            is_valid, error_msg = validate_domain_ban(wildcard_identifier)
            if not is_valid:
                await self.bot_send_message(
                    mto=ADMIN_ROOM,
                    mbody=error_msg,
                    mtype="groupchat",
                )
                return False
            target_type = "domain"
            target = domain
            row = await fetch_exact(target_type, target)
        elif is_jid:
            target_type = "jid"
            target = self.bare_jid(identifier)
            row = await fetch_exact(target_type, target)
        else:
            # A dotted bare value is ambiguous: it may be an existing domain
            # ban (``example.org``) or a valid nick (``john.doe``). Prefer an
            # exact domain ban, then fall back to an exact nick ban.
            if looks_like_domain(identifier):
                domain_candidate = identifier.strip(".")
                row = await fetch_exact("domain", domain_candidate)
                if row:
                    target_type = "domain"
                    target = domain_candidate
                    domain = domain_candidate

            if not row:
                target_type = "nick"
                target = identifier
                domain = None
                row = await fetch_exact(target_type, target)

            if not row:
                async with self.db.execute(
                    "SELECT jid, nick, until, issuer "
                    "FROM bans WHERE target_type = 'jid'"
                ) as cursor:
                    async for jid_db, nick_db, until_db, issuer_db in cursor:
                        if (
                            jid_db
                            and self.bare_jid(jid_db).split("@", 1)[0].lower()
                            == identifier
                        ):
                            row = (jid_db, nick_db, until_db, issuer_db)
                            target_type = "jid"
                            target = self.bare_jid(jid_db)
                            break

        if not row:
            await self.bot_send_message(
                mto=ADMIN_ROOM,
                mbody=f"❌ No ban found for {identifier}",
                mtype="groupchat",
            )
            return False

        ban_jid = row[0] if row[0] else None
        ban_nick = row[1] if row[1] else None
        ban_until = int(row[2] or 0)
        ban_issuer = row[3] or ""
        is_domain_ban = target_type == "domain"
        if is_domain_ban:
            domain = target

        # Clear server-side state before deleting the authoritative DB row. A
        # failed/partial room unban therefore keeps the ban in SQLite, allowing
        # the next sync to repair rooms instead of recovering a stale outcast as
        # a new permanent ban. This is used for manual and automatic unbans.
        if getattr(self, "reconnecting", False):
            log.info("Deferring unban for %s while reconnecting", identifier)
            if issuer != "system":
                await self.bot_send_message(
                    mto=ADMIN_ROOM,
                    mbody=(
                        f"❌ Unban deferred for {identifier}: XMPP reconnect is "
                        "in progress; ban kept."
                    ),
                    mtype="groupchat",
                )
            return False

        failed_rooms: list[str] = []
        for room in self.protected_rooms:
            try:
                removed = await self.apply_unban_to_room(
                    room,
                    ban_jid if not is_domain_ban else None,
                    ban_nick,
                    domain=domain if is_domain_ban else None,
                    announce=False,
                )
            except Exception as exc:  # noqa: BLE001 - per-room XMPP boundary
                log.warning("Error unbanning %s in %s: %s", identifier, room, exc)
                removed = False
            if removed is False:
                failed_rooms.append(room)

        if failed_rooms:
            rooms_text = ", ".join(sorted(failed_rooms))
            log.warning(
                "Ban %s retained in DB because room unban failed for: %s",
                identifier,
                rooms_text,
            )
            if issuer != "system":
                await self.bot_send_message(
                    mto=ADMIN_ROOM,
                    mbody=(
                        f"❌ Unban not completed for {identifier}; ban kept because "
                        f"server-side removal failed in: {rooms_text}"
                    ),
                    mtype="groupchat",
                )
            return False

        await self.db.execute(
            "DELETE FROM bans WHERE target_type = ? AND target = ?",
            (target_type, target),
        )
        await self.db.commit()

        # --- RTBL Publish: Withdraw permanent bans from own feed ---
        # Only permanent JID/domain bans are published, so only those need to
        # be retracted. Tempban expiry/unban should not touch the publish feed.
        if ban_until <= 0 and ban_issuer != "rtbl":
            if target_type == "jid" and ban_jid and not ban_jid.startswith("*."):
                await self.rtbl_retract_ban(jid=ban_jid, domain=None)
            elif target_type == "domain" and domain:
                await self.rtbl_retract_ban(jid=None, domain=domain)

        if is_domain_ban and domain:
            self._remove_domain_bans_from_cache(domain)
        else:
            self._remove_ban_from_cache(identifier, ban_jid=ban_jid, ban_nick=ban_nick)

        # Server-side removal succeeded everywhere. Emit public notices only
        # after DB/cache state has been committed, so a failed room removal can
        # never produce a misleading successful-unban announcement.
        if self.allow_user_cmds and ban_nick:
            for room in self.protected_rooms:
                if room == ADMIN_ROOM:
                    continue
                try:
                    await self.notify_protected(room, f"♻️ Unbanned {ban_nick}")
                except Exception as exc:  # noqa: BLE001 - notification is best effort
                    log.warning(
                        "Failed to announce unban for %s in %s: %s",
                        identifier,
                        room,
                        exc,
                    )

        if issuer == "system":
            msg_admin = f"♻️ Unbanned {identifier} (tempban expired)"
        else:
            msg_admin = f"♻️ Unbanned {identifier}" + (f" by {issuer}" if issuer else "")
        await self.bot_send_message(mto=ADMIN_ROOM, mbody=msg_admin, mtype="groupchat")
        log.info(msg_admin)
        event_type = "tempban_expired" if issuer == "system" else "unban_applied"
        self.log_event(logging.INFO, event_type, actor=issuer or "system", identifier=identifier, target_type=target_type, target=target, jid=ban_jid, nick=ban_nick)
        unban_details = {
            "identifier": identifier,
            "previous_until": ban_until,
            "previous_issuer": ban_issuer or None,
            "was_tempban": ban_until > 0,
        }
        await self.audit_event(
            event_type,
            actor=issuer or "system",
            target_type=target_type,
            target=target,
            jid=ban_jid,
            nick=ban_nick,
            details=unban_details,
        )
        if notify_policy and hasattr(self, "notify_policy_change"):
            await self.notify_policy_change(
                event_type,
                actor=issuer or "system",
                target=ban_jid or ban_nick or identifier,
            )

        return True
