"""Global ignorelist/whitelist protection for JIDs and domain-based bans."""

import logging

from .utils import (
    domain_matches,
    resolve_page,
    validate_domain_ban,
    validate_jid_format,
    wants_all_pages,
    without_all_pages_arg,
)

log = logging.getLogger(__name__)


class IgnorelistMixin:

    async def setup_ignorelist(self) -> None:
        """Create ignorelist table, migrate old RTBL ignorelist entries, and load into memory."""
        await self.db.execute("""
            CREATE TABLE IF NOT EXISTS ignorelist (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                target      TEXT    NOT NULL UNIQUE,
                target_type TEXT    NOT NULL,
                reason      TEXT,
                added_by    TEXT,
                created_at  INTEGER NOT NULL DEFAULT (strftime('%s','now'))
            )
        """)
        await self.db.execute(
            "CREATE INDEX IF NOT EXISTS idx_ignorelist_target ON ignorelist(target)"
        )

        # One-time compatibility migration from the old RTBL-only ignorelist.
        async with self.db.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'rtbl_ignorelist'"
        ) as cursor:
            old_table = await cursor.fetchone()

        if old_table:
            await self.db.execute("""
                INSERT OR IGNORE INTO ignorelist
                    (target, target_type, reason, added_by, created_at)
                SELECT
                    target, target_type, reason, added_by, created_at
                FROM rtbl_ignorelist
            """)

        await self.db.commit()
        await self._load_ignorelist_from_db()


    async def _load_ignorelist_from_db(self) -> None:
        """Load ignorelist from DB into in-memory sets."""
        ignore_jids: set[str] = set()
        ignore_domains: set[str] = set()

        async with self.db.execute(
            "SELECT target, target_type FROM ignorelist"
        ) as cursor:
            async for target, target_type in cursor:
                if not target:
                    continue
                if target_type == "jid":
                    ignore_jids.add(target.lower())
                elif target_type == "domain":
                    ignore_domains.add(target.lstrip("*." ).lower())

        self.ignore_jids = ignore_jids
        self.ignore_domains = ignore_domains

        log.debug(
            "Ignorelist loaded — %d JIDs, %d domains",
            len(ignore_jids), len(ignore_domains),
        )


    def is_ignored_jid(self, jid: str | None) -> bool:
        """Return True only for an exact bare-JID ignorelist match."""
        if not jid:
            return False

        bare = self.bare_jid(jid) if "@" in jid else jid.strip().lower()
        if not bare:
            return False

        return bare.lower() in getattr(self, "ignore_jids", set())


    def is_ignored_domain(self, domain_or_wildcard: str | None) -> bool:
        """Return True if a domain/wildcard target is covered by the ignorelist."""
        if not domain_or_wildcard:
            return False

        domain = domain_or_wildcard.strip().lower().lstrip("*.")
        if not domain:
            return False

        ignore_domains = getattr(self, "ignore_domains", set())
        if domain in ignore_domains:
            return True

        return any(domain_matches(domain, ignored_domain) for ignored_domain in ignore_domains)


    def is_ignored_target(
        self,
        target: str | None,
        *,
        include_domain_for_jid: bool = False,
    ) -> bool:
        """
        Return True if the target is protected by the ignorelist.

        Semantics:
        - Exact JID entries protect that JID from all bans.
        - Domain entries protect domain/wildcard bans and RTBL domain matches.
        - Domain entries do not block explicit manual JID bans unless
          include_domain_for_jid=True is requested by the caller.
        """
        if not target:
            return False

        candidate = target.strip().lower()
        if not candidate:
            return False

        if candidate.startswith("*." ):
            return self.is_ignored_domain(candidate)

        if "@" in candidate:
            bare = self.bare_jid(candidate)
            if not bare:
                return False
            if self.is_ignored_jid(bare):
                return True
            if include_domain_for_jid and "@" in bare:
                return self.is_ignored_domain(bare.split("@", 1)[1])
            return False

        # Plain domain target, e.g. example.org.
        if "." in candidate:
            return self.is_ignored_domain(candidate)

        return False


    async def _unban_matching_ignore_entries(self, target: str, target_type: str) -> None:
        """Remove active bans that are now protected by a newly added ignorelist entry."""
        if target_type == "jid":
            bare = self.bare_jid(target)
            if not bare:
                return

            async with self.db.execute(
                "SELECT 1 FROM bans WHERE target_type = 'jid' AND target = ? LIMIT 1",
                (bare,),
            ) as cursor:
                if await cursor.fetchone():
                    await self.unban_all(bare, issuer="ignorelist")
            return

        domain = target.lstrip("*.").lower()
        if not domain:
            return

        # Remove an exact wildcard-domain ban if one exists.
        async with self.db.execute(
            "SELECT 1 FROM bans WHERE target_type = 'domain' AND target = ? LIMIT 1",
            (domain,),
        ) as cursor:
            if await cursor.fetchone():
                await self.unban_all(f"*.{domain}", issuer="ignorelist")

        # Remove concrete JID bans that were applied from RTBL domain matches.
        # This lets `!ignore add user@example.org`, `!ignore add *.example.org`,
        # or the `!whitelist` alias immediately clear already-applied RTBL bans.
        async with self.db.execute(
            "SELECT jid FROM bans WHERE issuer = 'rtbl' AND target_type = 'jid' AND jid IS NOT NULL"
        ) as cursor:
            rows = [row[0] for row in await cursor.fetchall()]

        for jid in rows:
            bare = self.bare_jid(jid)
            if not bare or "@" not in bare:
                continue
            user_domain = bare.split("@", 1)[1].lower()
            if domain_matches(user_domain, domain):
                await self.unban_all(bare, issuer="ignorelist")


    async def cmd_ignore(
        self,
        args: list[str],
        room: str,
        actor: str = "unknown",
        command_name: str = "ignore",
    ) -> None:
        """
        Manage the global ignorelist.

        Exact JID entries are protected from all bans. Domain entries protect
        against domain-based bans and RTBL domain matches, but not explicit
        manual bans of individual JIDs on that domain.

        !ignore list [page]
        !ignore add <jid|domain> [reason]
        !ignore remove <jid|domain>

        !whitelist is accepted as an alias for !ignore.
        """
        p = self.command_prefix
        command_name = command_name if command_name in ("ignore", "whitelist") else "ignore"
        command = f"{p}{command_name}"
        label = "Whitelist" if command_name == "whitelist" else "Ignorelist"

        # Normalize alias/default forms before sub-command handling:
        #   !ignore / !whitelist           -> list
        #   !ignore all / !whitelist all   -> list all
        #   !ignore list all               -> list all
        raw_args = list(args or [])
        show_all = wants_all_pages(raw_args)
        args = without_all_pages_arg(raw_args)

        if not args:
            args = ["list"]
        elif args[0].lower() == "all":
            args = ["list"]

        sub_action = args[0].lower()

        # The alias is only an entrypoint. Listing should always point users to
        # the canonical ignorelist command and use the canonical list heading.
        list_label = "Ignorelist"
        list_command = f"{p}ignore"

        # ----------------------------------------------------------------
        # list
        # ----------------------------------------------------------------
        if sub_action == "list":
            async with self.db.execute(
                "SELECT COUNT(*) FROM ignorelist"
            ) as cursor:
                row = await cursor.fetchone()
                total = row[0] if row else 0

            page = 1
            if len(args) >= 2:
                if args[1].lower() == "last":
                    page = -1
                else:
                    try:
                        page = max(1, int(args[1]))
                    except ValueError:
                        log.debug("Invalid ignorelist page %r; using first page", args[1])

            if total == 0:
                await self.bot_send_message(
                    mto=room,
                    mbody=f"🚫 {list_label}:\n  (none)",
                    mtype="groupchat",
                )
                return

            per_page = 10
            if show_all:
                async with self.db.execute(
                    "SELECT target, target_type, reason, added_by FROM ignorelist "
                    "ORDER BY target_type, target",
                ) as cursor:
                    rows = await cursor.fetchall()

                resolved_page = 1
                lines = [f"🚫 {list_label} ({total}) - All:"]
            else:
                resolved_page = resolve_page(page, total, per_page)
                offset = (resolved_page - 1) * per_page
                total_pages = max(1, (total + per_page - 1) // per_page)

                async with self.db.execute(
                    "SELECT target, target_type, reason, added_by FROM ignorelist "
                    "ORDER BY target_type, target LIMIT ? OFFSET ?",
                    (per_page, offset),
                ) as cursor:
                    rows = await cursor.fetchall()

                lines = [f"🚫 {list_label} ({total}) - Page {resolved_page}/{total_pages}:"]
            for target, target_type, reason, added_by in rows:
                reason_str = f" — {reason}" if reason else ""
                added_str = f" (by {added_by})" if added_by else ""
                emoji = "🔑" if target_type == "jid" else "🌐"
                lines.append(f"  {emoji} {target}{reason_str}{added_str}")

            if not show_all and resolved_page < total_pages:
                lines.append(f"\nUse {list_command} list {resolved_page + 1} for the next page.")

            await self.bot_send_message(mto=room, mbody="\n".join(lines), mtype="groupchat")
            return

        # ----------------------------------------------------------------
        # add
        # ----------------------------------------------------------------
        if sub_action == "add":
            if len(args) < 2:
                await self.bot_send_message(
                    mto=room,
                    mbody=f"❌ Usage: {command} add <jid|domain> [reason]",
                    mtype="groupchat",
                )
                return

            raw_target = args[1].strip().lower()
            reason = " ".join(args[2:]) if len(args) > 2 else None

            if "@" in raw_target and not raw_target.startswith("*." ):
                if not validate_jid_format(raw_target):
                    await self.bot_send_message(
                        mto=room,
                        mbody=(
                            f"❌ Invalid JID for {label.lower()}: {raw_target}\n"
                            "Expected format: user@domain.tld"
                        ),
                        mtype="groupchat",
                    )
                    return

                target = raw_target
                target_type = "jid"
            else:
                is_valid_domain, _error_msg = validate_domain_ban(raw_target)
                if not is_valid_domain:
                    await self.bot_send_message(
                        mto=room,
                        mbody=(
                            f"❌ Invalid domain for {label.lower()}: {raw_target}\n"
                            "Expected format: domain.tld or *.domain.tld"
                        ),
                        mtype="groupchat",
                    )
                    return

                target = f"*.{raw_target.lstrip('*.').strip('.')}"
                target_type = "domain"

            await self.db.execute(
                """
                INSERT INTO ignorelist (target, target_type, reason, added_by)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(target) DO UPDATE SET
                    reason   = excluded.reason,
                    added_by = excluded.added_by
                """,
                (target, target_type, reason, actor),
            )
            await self.db.commit()
            await self._load_ignorelist_from_db()

            # Ensure the ignorelisted target is no longer actively banned.
            try:
                await self._unban_matching_ignore_entries(target, target_type)
            except Exception as e:
                log.warning(
                    "Ignorelist: failed to unban matching entries for %s after adding ignore entry: %s",
                    target,
                    e,
                )

            self.log_event(
                logging.INFO, "ignorelist_added",
                actor=actor, target_type=target_type, target=target, comment=reason,
            )
            await self.audit_event(
                "ignorelist_added", actor=actor,
                target_type=target_type, target=target, comment=reason,
            )

            await self.bot_send_message(
                mto=room,
                mbody=f"✅ {label}: Added {target}.",
                mtype="groupchat",
            )
            return

        # ----------------------------------------------------------------
        # remove
        # ----------------------------------------------------------------
        if sub_action in ("remove", "del", "delete"):
            if len(args) < 2:
                await self.bot_send_message(
                    mto=room,
                    mbody=f"❌ Usage: {command} remove <jid|domain>",
                    mtype="groupchat",
                )
                return

            raw_target = args[1].strip().lower()
            targets_to_try = sorted(set([
                raw_target,
                raw_target.lstrip("*."),
                f"*.{raw_target.lstrip('*.')}",
            ]))

            found = None
            found_type = None
            found_reason = None
            found_added_by = None

            for t in targets_to_try:
                async with self.db.execute(
                    """
                    SELECT target, target_type, reason, added_by
                    FROM ignorelist
                    WHERE target = ?
                    """,
                    (t,),
                ) as cursor:
                    row = await cursor.fetchone()

                if row:
                    found = row[0]
                    found_type = row[1]
                    found_reason = row[2]
                    found_added_by = row[3]
                    break

            if not found:
                await self.bot_send_message(
                    mto=room,
                    mbody=f"⚠️ {label}: {raw_target} was not found.",
                    mtype="groupchat",
                )
                return

            await self.db.execute("DELETE FROM ignorelist WHERE target = ?", (found,))
            await self.db.commit()
            await self._load_ignorelist_from_db()

            self.log_event(
                logging.INFO, "ignorelist_removed",
                actor=actor,
                target_type=found_type,
                target=found,
                comment=found_reason,
                previous_added_by=found_added_by,
            )
            await self.audit_event(
                "ignorelist_removed", actor=actor,
                target_type=found_type,
                target=found,
                comment=found_reason,
                details={"previous_added_by": found_added_by},
            )

            await self.bot_send_message(
                mto=room,
                mbody=f"✅ {label}: Removed {found}.",
                mtype="groupchat",
            )
            return

        await self.bot_send_message(
            mto=room,
            mbody=f"❌ Unknown sub-command: {sub_action}\nAvailable: list / add / remove",
            mtype="groupchat",
        )
