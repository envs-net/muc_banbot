"""Ban list, ban search, RTBL entry listing, and ban reason lookup commands."""

import time

from config import ADMIN_ROOM

from .utils import human_time, paginate_lines, resolve_page


class BanQueryMixin:
    @staticmethod
    def _ban_emoji(until: int, issuer: str | None) -> str:
        """Return the display icon for a ban entry."""
        if (issuer or "").lower() == "rtbl":
            return "🛡️"
        return "⏳" if until > 0 else "🔒"


    @staticmethod
    def _format_issuer_for_room(issuer: str | None, room: str) -> str:
        """Show full issuer in the admin room, but anonymize admins in protected rooms."""
        issuer_display = issuer or "unknown"
        if room != ADMIN_ROOM and issuer_display.lower() != "rtbl":
            return "admin"
        return issuer_display


    def _format_ban_match(self, jid, nick, until, issuer, comment, now):
        """Format a ban match for display in bansearch results."""
        remaining = human_time(max(0, until - now)) if until > 0 else "permanent"
        emoji = self._ban_emoji(until, issuer)
        issuer_display = self._format_issuer_for_room(issuer, ADMIN_ROOM)
        return (
            f"{emoji} {jid or nick or 'Unknown'} ({remaining}, by {issuer_display}"
            + (f", {comment}" if comment else "")
            + ")"
        )


    async def cmd_bansearch(self, query: str, page: int = 1) -> None:
        """
        Searches bans by JID, nick, domain, issuer, or comment/reason.
        Also searches RTBL hashes (by hashing the query if it looks like a JID,
        or by reason) and RTBL domain bans (by domain or reason).

        Supports optional filters:
        jid:<query>, nick:<query>, domain:<query>, issuer:<query>, by:<query>,
        comment:<query>, reason:<query>

        Usage: !bansearch <query> [page|last]
        """
        raw_query = query.strip()
        q = raw_query.lower()
        matches = []
        seen = set()
        now = int(time.time())

        field = None
        value = q

        for prefix in ("jid:", "nick:", "domain:", "issuer:", "by:", "comment:", "reason:"):
            if q.startswith(prefix):
                field = prefix[:-1]
                value = q[len(prefix):].strip()
                break

        if field == "by":
            field = "issuer"
        if field == "reason":
            field = "comment"

        if not value:
            self.send_message(
                mto=ADMIN_ROOM,
                mbody=f"❌ Usage: {self.command_prefix}bansearch <query> [page|last]",
                mtype="groupchat",
            )
            return

        def add_match(ban):
            jid, nick, until, issuer, comment = ban
            key = (jid or "", nick or "", until, issuer or "", comment or "")
            if key not in seen:
                seen.add(key)
                matches.append(self._format_ban_match(jid, nick, until, issuer, comment, now))

        # --- Regular bans ---
        if field is None:
            if value in self.ban_index_by_jid:
                add_match(self.ban_index_by_jid[value])

            if value in self.ban_index_by_nick:
                add_match(self.ban_index_by_nick[value])

            domain_value = value[2:] if value.startswith("*.") else value
            if domain_value in self.ban_index_by_domain:
                for ban in self.ban_index_by_domain[domain_value]:
                    add_match(ban)

        for _, (jid, nick, until, issuer, comment) in self.ban_cache.items():
            jid_value   = jid or ""
            nick_value  = nick or ""
            issuer_value  = issuer or ""
            comment_value = comment or ""

            if jid_value.startswith("*."):
                domain_value = jid_value[2:]
            elif "@" in jid_value:
                domain_value = jid_value.split("@", 1)[1]
            else:
                domain_value = ""

            fields = {
                "jid":     jid_value.lower(),
                "nick":    nick_value.lower(),
                "domain":  domain_value.lower(),
                "issuer":  issuer_value.lower(),
                "comment": comment_value.lower(),
            }

            if field:
                haystack = fields.get(field, "")
            else:
                haystack = " ".join(v for v in fields.values() if v)

            if value in haystack:
                add_match((jid, nick, until, issuer, comment))

        # --- RTBL domain bans ---
        rtbl_domain_matches = []
        if getattr(self, "rtbl_enabled", False) and field in (None, "domain", "comment"):
            async with self.db.execute(
                "SELECT domain, service_jid, node, reason FROM rtbl_domains"
            ) as cursor:
                async for domain, service_jid, node, reason in cursor:
                    if field == "domain":
                        haystack = domain.lower()
                    elif field == "comment":
                        haystack = (reason or "").lower()
                    else:
                        haystack = f"{domain} {service_jid} {node} {reason or ''}".lower()

                    if value in haystack:
                        reason_str = f" — {reason}" if reason else ""
                        rtbl_domain_matches.append(
                            f"🌐 *.{domain}  [{service_jid}/{node}]{reason_str}"
                        )

        # --- RTBL JID hashes ---
        rtbl_hash_matches = []
        if getattr(self, "rtbl_enabled", False) and field in (None, "jid", "comment"):
            if field in (None, "jid") and "@" in value:
                # Hash the query JID and do an exact lookup
                h = self._rtbl_hash_jid(value)
                async with self.db.execute(
                    "SELECT hash, service_jid, node, reason FROM rtbl_hashes WHERE hash = ?",
                    (h,),
                ) as cursor:
                    row = await cursor.fetchone()
                if row:
                    hash_val, service_jid, node, reason = row
                    reason_str = f" — {reason}" if reason else ""
                    rtbl_hash_matches.append(
                        f"🔑 {hash_val[:16]}…  [{service_jid}/{node}]{reason_str}  (matched JID hash)"
                    )
            elif field in (None, "comment"):
                # Search by reason/service/node text
                async with self.db.execute(
                    "SELECT hash, service_jid, node, reason FROM rtbl_hashes"
                ) as cursor:
                    async for hash_val, service_jid, node, reason in cursor:
                        haystack = f"{service_jid} {node} {reason or ''}".lower()
                        if value in haystack:
                            reason_str = f" — {reason}" if reason else ""
                            rtbl_hash_matches.append(
                                f"🔑 {hash_val[:16]}…  [{service_jid}/{node}]{reason_str}"
                            )

        # --- Collect all result entries ---
        all_entries = []

        if matches:
            all_entries.extend(matches)

        if rtbl_domain_matches:
            all_entries.extend(rtbl_domain_matches)

        if rtbl_hash_matches:
            all_entries.extend(rtbl_hash_matches)

        if not all_entries:
            self.send_message(
                mto=ADMIN_ROOM,
                mbody=f"❌ No bans found matching '{raw_query}'.",
                mtype="groupchat",
            )
            return

        # Section headers for context (not paginated, prepended to output)
        section_info = []
        if matches:
            section_info.append(f"🔍 Regular bans: {len(matches)}")
        if rtbl_domain_matches:
            section_info.append(f"🌐 RTBL domains: {len(rtbl_domain_matches)}")
        if rtbl_hash_matches:
            section_info.append(f"🔑 RTBL hashes: {len(rtbl_hash_matches)}")

        per_page      = 10
        resolved_page = resolve_page(page, len(all_entries), per_page)
        page_lines, current_page, total_pages, total_items = paginate_lines(
            all_entries, resolved_page, per_page=per_page
        )

        header = (
            f"🔍 Bansearch '{raw_query}' ({total_items}) - Page {current_page}/{total_pages}"
            f"  [{', '.join(section_info)}]"
        )
        text = header + ":\n" + "\n".join(page_lines)

        if current_page < total_pages:
            text += f"\n\nUse {self.command_prefix}bansearch {raw_query} {current_page + 1} for the next page."

        self.send_message(mto=ADMIN_ROOM, mbody=text, mtype="groupchat")


    async def cmd_banlist(self, room: str, page: int = 1) -> None:
        """
        Show bans with pagination.
        Admin Room: full info (all bans)
        Protected Rooms: temporary bans only, anonymized
        """
        async with self.db.execute(
            "SELECT jid, nick, until, issuer, comment FROM bans ORDER BY "
            "CASE WHEN until <= 0 THEN 1 ELSE 0 END, until ASC, LOWER(COALESCE(nick, jid)) ASC"
        ) as cursor:
            rows = await cursor.fetchall()

        if not rows:
            text = "📋 Banlist:\nNo active bans."
        else:
            now = int(time.time())
            entries = []

            for jid, nick, until, issuer, comment in rows:
                # Skip expired tempbans
                if until > 0 and until <= now:
                    continue

                # Skip permanent bans in protected rooms
                if room != ADMIN_ROOM and until <= 0:
                    continue

                remaining = human_time(max(0, until - now)) if until > 0 else "permanent"
                emoji = self._ban_emoji(until, issuer)
                issuer_display = self._format_issuer_for_room(issuer, room)

                if jid and jid.startswith("*."):
                    display = jid
                elif room == ADMIN_ROOM:
                    display = jid or nick or "Unknown"
                else:
                    display = nick or (jid.split("@")[0] if jid else "Unknown")

                entry = (
                    f"{emoji} {display} ({remaining}, by {issuer_display}"
                    + (f", {comment}" if comment else "")
                    + ")"
                )
                entries.append(entry)

            if not entries:
                text = "📋 Banlist:\nNo active temporary bans." if room != ADMIN_ROOM else "📋 Banlist:\nNo active bans."
            else:
                per_page = 10
                resolved_page = resolve_page(page, len(entries), per_page)
                page_lines, current_page, total_pages, total_items = paginate_lines(
                    entries, resolved_page, per_page=per_page
                )

                header = f"📋 Banlist ({total_items}) - Page {current_page}/{total_pages}:"
                text = header + "\n" + "\n".join(page_lines)

                if current_page < total_pages:
                    text += f"\n\nUse {self.command_prefix}banlist {current_page + 1} for the next page."

        self.send_message(mto=room, mbody=text, mtype="groupchat")


    async def cmd_banlist_rtbl(self, room: str, page: int = 1) -> None:
        """
        Show entries from all RTBL subscriptions (hashes + domains).
        Admin room only. Groups entries by subscription source.
        """
        if not getattr(self, "rtbl_enabled", False):
            self.send_message(
                mto=room,
                mbody="❌ RTBL is disabled.",
                mtype="groupchat",
            )
            return

        entries = []

        # --- JID hashes ---
        async with self.db.execute(
            """
            SELECT hash, service_jid, node, reason, created_at
            FROM rtbl_hashes
            ORDER BY service_jid, node, created_at DESC
            """
        ) as cursor:
            hash_rows = await cursor.fetchall()

        for hash_val, service_jid, node, reason, created_at in hash_rows:
            label = f"[{service_jid}/{node}]"
            reason_str = f" — {reason}" if reason else ""
            entries.append(f"🔑 {hash_val[:16]}…  {label}{reason_str}")

        # --- Domain bans ---
        async with self.db.execute(
            """
            SELECT domain, service_jid, node, reason, created_at
            FROM rtbl_domains
            ORDER BY service_jid, node, domain ASC
            """
        ) as cursor:
            domain_rows = await cursor.fetchall()

        for domain, service_jid, node, reason, created_at in domain_rows:
            label = f"[{service_jid}/{node}]"
            reason_str = f" — {reason}" if reason else ""
            entries.append(f"🌐 *.{domain}  {label}{reason_str}")

        if not entries:
            self.send_message(
                mto=room,
                mbody="🛡️ RTBL Banlist:\nNo RTBL entries in database.",
                mtype="groupchat",
            )
            return

        per_page = 10
        resolved_page = resolve_page(page, len(entries), per_page)
        page_lines, current_page, total_pages, total_items = paginate_lines(
            entries, resolved_page, per_page=per_page
        )

        text = (
            f"🛡️ RTBL Banlist ({total_items}) - Page {current_page}/{total_pages}:\n"
            + "\n".join(page_lines)
        )
        if current_page < total_pages:
            text += f"\n\nUse {self.command_prefix}banlist rtbl {current_page + 1} for the next page."

        self.send_message(mto=room, mbody=text, mtype="groupchat")


    async def cmd_why(self, identifier: str, room: str) -> None:
        """
        Show reason for a ban.
        Admin Room: full info (JID/nick)
        Protected Rooms: only nick, JID anonymized
        """
        is_jid = "@" in identifier
        ban_jid = identifier if is_jid else None
        ban_nick = None if is_jid else identifier.lower()
        row = None

        # Check JID
        if ban_jid:
            async with self.db.execute(
                "SELECT jid, nick, until, issuer, comment FROM bans WHERE jid=?", (ban_jid,)
            ) as cursor:
                row = await cursor.fetchone()

        # Check nick
        if not row:
            async with self.db.execute(
                "SELECT jid, nick, until, issuer, comment FROM bans WHERE LOWER(nick)=?", (ban_nick,)
            ) as cursor:
                row = await cursor.fetchone()

        # Fallback nick-only check against JIDs
        if not row and ban_nick:
            async with self.db.execute("SELECT jid, nick, until, issuer, comment FROM bans") as cursor:
                async for jid_db, nick_db, until, issuer, comment in cursor:
                    if jid_db and self.bare_jid(jid_db).split("@")[0].lower() == ban_nick:
                        row = (jid_db, nick_db, until, issuer, comment)
                        break

        if row:
            jid_db, nick_db, until, issuer, comment = row
            now = int(time.time())
            remaining = human_time(max(0, until - now)) if until > 0 else "permanent"
            emoji = self._ban_emoji(until, issuer)
            issuer_display = self._format_issuer_for_room(issuer, room)

            if room == ADMIN_ROOM:
                display = jid_db or nick_db or identifier
            else:
                display = nick_db or (jid_db.split("@")[0] if jid_db else identifier)

            msg = (
                f"{emoji} {display} ({remaining}, by {issuer_display}"
                + (f", {comment}" if comment else "")
                + ")"
            )
        else:
            msg = f"No ban found for {identifier}"

        if room == ADMIN_ROOM:
            q = identifier.lower().strip()
            like = f"%{q}%"
            async with self.db.execute(
                """
                SELECT created_at, event_type, actor, target_type, target, jid, nick, until, comment, details
                FROM audit_log
                WHERE LOWER(COALESCE(target, '')) LIKE ?
                   OR LOWER(COALESCE(jid, '')) LIKE ?
                   OR LOWER(COALESCE(nick, '')) LIKE ?
                   OR LOWER(COALESCE(details, '')) LIKE ?
                ORDER BY created_at DESC, id DESC
                LIMIT 3
                """,
                (like, like, like, like),
            ) as cursor:
                audit_rows = await cursor.fetchall()
            if audit_rows:
                msg += "\n\nRecent audit history:\n" + "\n".join(self._format_audit_row(r) for r in audit_rows)

        self.send_message(mto=room, mbody=msg, mtype="groupchat")
