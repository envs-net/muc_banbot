"""RTBL (Real-Time Block List) PubSub support.

Two item types are handled within RTBL nodes:
  - JID bans   : SHA-256 hashed bare JIDs (64 lowercase hex chars)
                 Compatible with xmppbl.org muc_bans_sha256.
  - Domain bans: plaintext domain names (e.g. 'spam.example.org')
                 Applied as wildcard bans (*.domain).

Item type is detected automatically from the item ID format when
receiving from any subscribed node.

Own publish feed (optional, requires a PubSub service on the local server):
  - JID bans    -> RTBL_PUBLISH_JID_NODE    (default: muc_bans_sha256)
  - Domain bans -> RTBL_PUBLISH_DOMAIN_NODE (default: muc_bans_domains)

Bans from subscribed RTBL feeds are stored in separate tables
(rtbl_hashes, rtbl_domains) and are NEVER written to the main bans table.
Admin/owner protection is always enforced before any RTBL ban is applied.
"""

import hashlib
import logging
import re

from slixmpp.exceptions import IqError, IqTimeout

log = logging.getLogger(__name__)

# Matches exactly 64 lowercase hex characters (SHA-256 digest)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
# Matches a plain domain name or wildcard domain (no @, contains a dot)
_DOMAIN_RE = re.compile(r"^[\w*][\w\-.*]*\.[a-z]{2,}$")


def _is_sha256(value: str) -> bool:
    """Return True if value looks like a SHA-256 hex digest."""
    return bool(_SHA256_RE.match(value))


def _is_domain(value: str) -> bool:
    """Return True if value looks like a (wildcard) domain name."""
    return "@" not in value and bool(_DOMAIN_RE.match(value.lstrip("*.")))


class RtblMixin:

    # ------------------------------------------------------------------
    # DB setup and in-memory cache
    # ------------------------------------------------------------------

    async def setup_rtbl(self) -> None:
        """
        Create RTBL tables, load subscriptions and cached entries from DB,
        register PubSub event handlers (once per process lifetime), then
        subscribe to every stored node and fetch its current items.

        Called from start() on every (re)connect.
        """
        if not getattr(self, "rtbl_enabled", False):
            return

        await self.db.execute("""
            CREATE TABLE IF NOT EXISTS rtbl_subscriptions (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                service_jid TEXT    NOT NULL,
                node        TEXT    NOT NULL,
                created_at  INTEGER NOT NULL DEFAULT (strftime('%s','now')),
                UNIQUE(service_jid, node)
            )
        """)
        await self.db.execute("""
            CREATE TABLE IF NOT EXISTS rtbl_hashes (
                hash        TEXT    NOT NULL,
                service_jid TEXT    NOT NULL,
                node        TEXT    NOT NULL,
                reason      TEXT,
                created_at  INTEGER NOT NULL DEFAULT (strftime('%s','now')),
                PRIMARY KEY (hash, service_jid, node)
            )
        """)
        await self.db.execute(
            "CREATE INDEX IF NOT EXISTS idx_rtbl_hashes_hash ON rtbl_hashes(hash)"
        )
        await self.db.execute("""
            CREATE TABLE IF NOT EXISTS rtbl_domains (
                domain      TEXT    NOT NULL,
                service_jid TEXT    NOT NULL,
                node        TEXT    NOT NULL,
                reason      TEXT,
                created_at  INTEGER NOT NULL DEFAULT (strftime('%s','now')),
                PRIMARY KEY (domain, service_jid, node)
            )
        """)
        await self.db.execute(
            "CREATE INDEX IF NOT EXISTS idx_rtbl_domains_domain ON rtbl_domains(domain)"
        )
        await self.db.commit()

        # Register handlers only once — they survive reconnects
        if not getattr(self, "_rtbl_handlers_registered", False):
            self.add_event_handler("pubsub_publish", self._on_rtbl_publish)
            self.add_event_handler("pubsub_retract", self._on_rtbl_retract)
            self._rtbl_handlers_registered = True

        await self._load_rtbl_subscriptions_from_db()

        for service_jid, node in list(self.rtbl_subscriptions):
            await self._rtbl_subscribe_and_fetch(service_jid, node)


    async def _load_rtbl_subscriptions_from_db(self) -> None:
        """Load subscription list from DB and rebuild in-memory caches."""
        async with self.db.execute(
            "SELECT service_jid, node FROM rtbl_subscriptions ORDER BY created_at"
        ) as cursor:
            rows = await cursor.fetchall()

        self.rtbl_subscriptions = [(r[0], r[1]) for r in rows]
        await self._rebuild_rtbl_caches()
        log.info("RTBL: Loaded %d subscription(s)", len(self.rtbl_subscriptions))


    async def _rebuild_rtbl_caches(self) -> None:
        """
        Rebuild both in-memory lookup caches from DB.

        rtbl_hash_cache   : hash   -> reason | None
        rtbl_domain_cache : domain -> reason | None

        When the same entry appears in multiple subscriptions the most
        recently stored reason is kept (DB iteration order).
        """
        hash_cache: dict[str, str | None] = {}
        async with self.db.execute("SELECT hash, reason FROM rtbl_hashes") as cursor:
            async for hash_val, reason in cursor:
                hash_cache[hash_val] = reason
        self.rtbl_hash_cache = hash_cache

        domain_cache: dict[str, str | None] = {}
        async with self.db.execute("SELECT domain, reason FROM rtbl_domains") as cursor:
            async for domain, reason in cursor:
                domain_cache[domain] = reason
        self.rtbl_domain_cache = domain_cache

        log.debug(
            "RTBL: Caches rebuilt — %d hashes, %d domains",
            len(hash_cache),
            len(domain_cache),
        )

    # ------------------------------------------------------------------
    # PubSub subscribe and initial item fetch
    # ------------------------------------------------------------------

    async def _rtbl_subscribe_and_fetch(self, service_jid: str, node: str) -> None:
        """Subscribe to a PubSub node and fetch all existing items."""
        try:
            await self.plugin["xep_0060"].subscribe(service_jid, node)
            log.info("RTBL: Subscribed to '%s' @ %s", node, service_jid)
        except (IqError, IqTimeout) as e:
            log.warning("RTBL: Could not subscribe to '%s' @ %s: %s", node, service_jid, e)

        await self._rtbl_fetch_all_items(service_jid, node)


    async def _rtbl_fetch_all_items(self, service_jid: str, node: str) -> None:
        """
        Fetch all current items from an RTBL node using manual RSM pagination
        (XEP-0059) via fully raw XML IQ construction.
        Items are classified as JID hashes or domain bans by their ID format.
        Unknown formats are skipped with a debug log entry.
        """
        from config import ADMIN_ROOM
        from xml.etree import ElementTree as ET

        _PUBSUB = "http://jabber.org/protocol/pubsub"
        _RSM    = "http://jabber.org/protocol/rsm"

        hash_count   = 0
        domain_count = 0
        page         = 0
        last_id      = None
        page_size    = 200

        while True:
            try:
                iq = self.make_iq_get(ito=service_jid)

                # Build <pubsub><items node="..."/><set><max>N</max>[<after>…</after>]</set></pubsub>
                pubsub_el = ET.Element(f"{{{_PUBSUB}}}pubsub")
                items_el  = ET.SubElement(pubsub_el, f"{{{_PUBSUB}}}items")
                items_el.set("node", node)

                rsm_set  = ET.SubElement(pubsub_el, f"{{{_RSM}}}set")
                max_el   = ET.SubElement(rsm_set, f"{{{_RSM}}}max")
                max_el.text = str(page_size)
                if last_id is not None:
                    after_el      = ET.SubElement(rsm_set, f"{{{_RSM}}}after")
                    after_el.text = last_id

                # Replace any existing payload with our hand-built element
                for child in list(iq.xml):
                    iq.xml.remove(child)
                iq.xml.append(pubsub_el)

                result = await iq.send()

                # Parse items from raw XML response
                result_pubsub = result.xml.find(f"{{{_PUBSUB}}}pubsub")
                if result_pubsub is None:
                    break
                result_items_el = result_pubsub.find(f"{{{_PUBSUB}}}items")
                items = list(result_items_el) if result_items_el is not None else []

            except (IqError, IqTimeout) as e:
                log.warning(
                    "RTBL: Could not fetch items from '%s' @ %s (page %d): %s",
                    node, service_jid, page, e,
                )
                break

            if not items:
                break

            for item_el in items:
                item_id = item_el.get("id", "").lower().strip()
                if not item_id:
                    continue

                # Extract reason from payload child element
                payload = item_el[0] if len(item_el) > 0 else None
                reason  = self._rtbl_extract_reason(payload)

                if _is_sha256(item_id):
                    await self.db.execute(
                        """
                        INSERT INTO rtbl_hashes (hash, service_jid, node, reason)
                        VALUES (?, ?, ?, ?)
                        ON CONFLICT(hash, service_jid, node) DO UPDATE SET
                            reason = excluded.reason
                        """,
                        (item_id, service_jid, node, reason),
                    )
                    hash_count += 1

                elif _is_domain(item_id):
                    domain = item_id.lstrip("*.")
                    await self.db.execute(
                        """
                        INSERT INTO rtbl_domains (domain, service_jid, node, reason)
                        VALUES (?, ?, ?, ?)
                        ON CONFLICT(domain, service_jid, node) DO UPDATE SET
                            reason = excluded.reason
                        """,
                        (domain, service_jid, node, reason),
                    )
                    domain_count += 1

                else:
                    log.debug(
                        "RTBL: Skipping unrecognised item ID '%s' from %s/%s",
                        item_id, service_jid, node,
                    )

            await self.db.commit()
            page   += 1
            last_id = items[-1].get("id") if items else None

            log.debug(
                "RTBL: Page %d — %d hashes, %d domains so far (last: %s…)",
                page, hash_count, domain_count,
                last_id[:12] if last_id else "none",
            )

            # Read RSM <last> from the response XML to detect end of result set
            rsm_el   = result.xml.find(f".//{{{_RSM}}}set")
            rsm_last = None
            if rsm_el is not None:
                last_el  = rsm_el.find(f"{{{_RSM}}}last")
                rsm_last = last_el.text.strip() if last_el is not None and last_el.text else None

            log.debug("RTBL: RSM last=%s", rsm_last)

            if not rsm_last or len(items) < page_size:
                break

        await self._rebuild_rtbl_caches()

        log.info(
            "RTBL: Fetched from '%s' @ %s — %d hashes, %d domains (%d pages)",
            node, service_jid, hash_count, domain_count, page,
        )

        if self.rtbl_announce and (hash_count + domain_count) > 0:
            self.send_message(
                mto=ADMIN_ROOM,
                mbody=(
                    f"🛡️ RTBL: Loaded from {service_jid} (node: {node}) — "
                    f"{hash_count} JID hashes, {domain_count} domain bans"
                ),
                mtype="groupchat",
            )

    # ------------------------------------------------------------------
    # PubSub live event handlers
    # ------------------------------------------------------------------

    async def _on_rtbl_publish(self, msg) -> None:
        """
        Handle an incoming PubSub publish event.
        Only processes events from nodes we are subscribed to.
        Classifies each item and stores/applies it accordingly.
        """
        if not getattr(self, "rtbl_enabled", False):
            return

        try:
            node        = msg["pubsub_event"]["items"]["node"]
            service_jid = msg["from"].bare
        except Exception:
            return

        subscribed = {(s.lower(), n) for s, n in self.rtbl_subscriptions}
        if (service_jid.lower(), node) not in subscribed:
            return

        for item in msg["pubsub_event"]["items"]:
            try:
                item_id = item["id"].lower().strip()
            except Exception:
                continue
            if not item_id:
                continue

            reason = self._rtbl_extract_reason(item.get("payload"))

            if _is_sha256(item_id):
                await self.db.execute(
                    """
                    INSERT INTO rtbl_hashes (hash, service_jid, node, reason)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(hash, service_jid, node) DO UPDATE SET
                        reason = excluded.reason
                    """,
                    (item_id, service_jid, node, reason),
                )
                await self.db.commit()
                self.rtbl_hash_cache[item_id] = reason
                log.info(
                    "RTBL: New JID hash from %s/%s: %s… (reason: %s)",
                    service_jid, node, item_id[:12], reason,
                )
                await self._rtbl_check_all_occupants_for_hash(item_id, reason)

            elif _is_domain(item_id):
                domain = item_id.lstrip("*.")
                await self.db.execute(
                    """
                    INSERT INTO rtbl_domains (domain, service_jid, node, reason)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(domain, service_jid, node) DO UPDATE SET
                        reason = excluded.reason
                    """,
                    (domain, service_jid, node, reason),
                )
                await self.db.commit()
                self.rtbl_domain_cache[domain] = reason
                log.info(
                    "RTBL: New domain ban from %s/%s: *.%s (reason: %s)",
                    service_jid, node, domain, reason,
                )
                await self._rtbl_check_all_occupants_for_domain(domain, reason)

            else:
                log.debug(
                    "RTBL: Skipping unrecognised item '%s' from %s/%s",
                    item_id, service_jid, node,
                )


    async def _on_rtbl_retract(self, msg) -> None:
        """
        Handle a PubSub retract event.
        Removes the item from DB and from the in-memory cache, but only if
        no other subscription still references the same entry.
        """
        if not getattr(self, "rtbl_enabled", False):
            return

        try:
            node        = msg["pubsub_event"]["items"]["node"]
            service_jid = msg["from"].bare
        except Exception:
            return

        subscribed = {(s.lower(), n) for s, n in self.rtbl_subscriptions}
        if (service_jid.lower(), node) not in subscribed:
            return

        for item in msg["pubsub_event"]["items"]:
            try:
                item_id = item["id"].lower().strip()
            except Exception:
                continue
            if not item_id:
                continue

            if _is_sha256(item_id):
                await self.db.execute(
                    "DELETE FROM rtbl_hashes WHERE hash = ? AND service_jid = ? AND node = ?",
                    (item_id, service_jid, node),
                )
                await self.db.commit()
                # Remove from cache only when no other subscription still carries it
                async with self.db.execute(
                    "SELECT 1 FROM rtbl_hashes WHERE hash = ? LIMIT 1", (item_id,)
                ) as cursor:
                    if not await cursor.fetchone():
                        self.rtbl_hash_cache.pop(item_id, None)
                log.info(
                    "RTBL: JID hash retracted from %s/%s: %s…",
                    service_jid, node, item_id[:12],
                )

            elif _is_domain(item_id):
                domain = item_id.lstrip("*.")
                await self.db.execute(
                    "DELETE FROM rtbl_domains WHERE domain = ? AND service_jid = ? AND node = ?",
                    (domain, service_jid, node),
                )
                await self.db.commit()
                async with self.db.execute(
                    "SELECT 1 FROM rtbl_domains WHERE domain = ? LIMIT 1", (domain,)
                ) as cursor:
                    if not await cursor.fetchone():
                        self.rtbl_domain_cache.pop(domain, None)
                log.info(
                    "RTBL: Domain ban retracted from %s/%s: *.%s",
                    service_jid, node, domain,
                )

    # ------------------------------------------------------------------
    # Join-time check (called from muc_online)
    # ------------------------------------------------------------------

    async def check_jid_against_rtbl(self, jid: str, nick: str) -> bool:
        """
        Check a joining user against both in-memory RTBL caches.

        1. SHA-256 hash of the bare JID is looked up in rtbl_hash_cache.
        2. The user's domain (and all parent domains) is checked against
           rtbl_domain_cache using suffix matching.

        Returns True if a ban was applied so that muc_online() can skip
        further processing for this user.
        """
        if not getattr(self, "rtbl_enabled", False):
            return False

        bare = self.bare_jid(jid)
        if not bare:
            return False

        # JID hash check
        h = self._rtbl_hash_jid(bare)
        if h in self.rtbl_hash_cache:
            await self._rtbl_apply_ban_jid(bare, nick, self.rtbl_hash_cache[h])
            return True

        # Domain check
        if "@" in bare:
            user_domain = bare.split("@", 1)[1].lower()
            for banned_domain, reason in self.rtbl_domain_cache.items():
                if _domain_matches(user_domain, banned_domain):
                    await self._rtbl_apply_ban_domain(banned_domain, reason, nick=nick, jid=bare)
                    return True

        return False


    async def _rtbl_check_all_occupants_for_hash(
        self, hash_val: str, reason: str | None
    ) -> None:
        """
        Scan all current occupants of every protected room against a
        newly received JID hash.  Called immediately on pubsub_publish.
        """
        for room, occupants in self.occupants.items():
            if room not in self.protected_rooms:
                continue
            for nick, info in list(occupants.items()):
                jid = info.get("jid")
                if not jid:
                    continue
                bare = self.bare_jid(jid)
                if bare and self._rtbl_hash_jid(bare) == hash_val:
                    await self._rtbl_apply_ban_jid(bare, nick, reason)


    async def _rtbl_check_all_occupants_for_domain(
        self, domain: str, reason: str | None
    ) -> None:
        """
        Scan all current occupants of every protected room against a
        newly received domain ban.  Called immediately on pubsub_publish.
        """
        for room, occupants in self.occupants.items():
            if room not in self.protected_rooms:
                continue
            for nick, info in list(occupants.items()):
                jid = info.get("jid")
                if not jid:
                    continue
                bare = self.bare_jid(jid)
                if not bare or "@" not in bare:
                    continue
                user_domain = bare.split("@", 1)[1].lower()
                if _domain_matches(user_domain, domain):
                    await self._rtbl_apply_ban_domain(domain, reason, nick=nick, jid=bare)

    # ------------------------------------------------------------------
    # Ban application (never writes to the main bans table)
    # ------------------------------------------------------------------

    async def _rtbl_apply_ban_jid(
        self,
        jid: str,
        nick: str | None,
        reason: str | None,
    ) -> None:
        """
        Apply an RTBL JID ban via MUC outcast affiliation in all protected rooms.

        Admin/owner protection is always checked first.  If the target is an
        admin or owner in any protected room or the admin room the ban is
        silently dropped and, if RTBL_ANNOUNCE is True, a warning is sent to
        the admin room.

        The ban is NOT written to the main bans table.
        """
        from config import ADMIN_ROOM

        protected, protect_reason = await self.is_protected_admin_target(
            jid, nick=nick, jid=jid
        )
        if protected:
            log.warning(
                "RTBL: Ignoring JID ban for %s — admin/owner protected: %s",
                jid, protect_reason,
            )
            if self.rtbl_announce:
                self.send_message(
                    mto=ADMIN_ROOM,
                    mbody=(
                        f"⚠️ RTBL: Ignored JID ban for {jid} "
                        f"— protected admin/owner ({protect_reason})"
                    ),
                    mtype="groupchat",
                )
            return

        comment = f"RTBL: {reason}" if reason else "RTBL ban"
        log.info("RTBL: Banning JID %s (reason: %s)", jid, reason)

        if self.rtbl_announce:
            self.send_message(
                mto=ADMIN_ROOM,
                mbody=f"🛡️ RTBL: Banning {jid}" + (f" — {reason}" if reason else ""),
                mtype="groupchat",
            )

        self.log_event(
            logging.INFO, "rtbl_ban_applied",
            actor="rtbl", identifier=jid, target_type="jid",
            target=jid, jid=jid, nick=nick, comment=comment,
        )
        await self.audit_event(
            "rtbl_ban_applied", actor="rtbl", target_type="jid",
            target=jid, jid=jid, nick=nick, comment=comment,
        )

        for room in self.protected_rooms:
            try:
                await self.apply_ban_to_room(room, jid, nick, comment, issuer="rtbl")
            except Exception as e:
                log.warning("RTBL: Failed to apply JID ban in %s: %s", room, e)


    async def _rtbl_apply_ban_domain(
        self,
        domain: str,
        reason: str | None,
        nick: str | None = None,
        jid: str | None = None,
    ) -> None:
        """
        Apply an RTBL domain ban to all currently matching occupants in every
        protected room via MUC outcast affiliation.

        Admin/owner protection is always checked first (same as a manual !ban).
        The ban is NOT written to the main bans table.
        """
        from config import ADMIN_ROOM
        from .utils import domain_matches

        wildcard = f"*.{domain}"

        protected, protect_reason = await self.is_protected_admin_target(
            wildcard, nick=nick, jid=jid
        )
        if protected:
            log.warning(
                "RTBL: Ignoring domain ban *.%s — admin/owner protected: %s",
                domain, protect_reason,
            )
            if self.rtbl_announce:
                self.send_message(
                    mto=ADMIN_ROOM,
                    mbody=(
                        f"⚠️ RTBL: Ignored domain ban *.{domain} "
                        f"— protected admin/owner ({protect_reason})"
                    ),
                    mtype="groupchat",
                )
            return

        comment = f"RTBL: {reason}" if reason else "RTBL domain ban"
        log.info("RTBL: Applying domain ban *.%s (reason: %s)", domain, reason)

        if self.rtbl_announce:
            self.send_message(
                mto=ADMIN_ROOM,
                mbody=f"🛡️ RTBL: Domain ban *.{domain}" + (f" — {reason}" if reason else ""),
                mtype="groupchat",
            )

        self.log_event(
            logging.INFO, "rtbl_ban_applied",
            actor="rtbl", identifier=wildcard, target_type="domain",
            target=domain, jid=wildcard, nick=nick, comment=comment,
        )
        await self.audit_event(
            "rtbl_ban_applied", actor="rtbl", target_type="domain",
            target=domain, jid=wildcard, nick=nick, comment=comment,
        )

        for room in self.protected_rooms:
            try:
                for occ_nick, info in list(self.occupants.get(room, {}).items()):
                    occ_jid = info.get("jid")
                    if not occ_jid:
                        continue
                    bare = self.bare_jid(occ_jid)
                    if not bare or "@" not in bare:
                        continue
                    occ_domain = bare.split("@", 1)[1].lower()
                    if domain_matches(occ_domain, domain):
                        await self.apply_ban_to_room(
                            room, bare, occ_nick, comment, issuer="rtbl"
                        )
            except Exception as e:
                log.warning("RTBL: Failed to apply domain ban in %s: %s", room, e)

    # ------------------------------------------------------------------
    # Helper utilities
    # ------------------------------------------------------------------

    @staticmethod
    def _rtbl_hash_jid(jid: str) -> str:
        """Return the SHA-256 hex digest of a bare JID (normalised to lowercase)."""
        return hashlib.sha256(jid.strip().lower().encode()).hexdigest()

    @staticmethod
    def _rtbl_extract_reason(payload) -> str | None:
        """
        Extract a human-readable reason string from a PubSub item payload.
        Supports XEP-0377 <report xmlns='urn:xmpp:reporting:1'> and a
        generic <text> fallback.
        """
        if payload is None:
            return None
        try:
            xml_el = getattr(payload, "xml", payload)
            el = xml_el.find(".//{urn:xmpp:reporting:1}text")
            if el is not None and el.text:
                return el.text.strip()
            el = xml_el.find(".//text")
            if el is not None and el.text:
                return el.text.strip()
        except Exception:
            pass
        return None

    @staticmethod
    def _rtbl_build_payload(comment: str | None):
        """
        Build a XEP-0377 <report xmlns='urn:xmpp:reporting:1'> XML element
        with an optional <text> child carrying the ban reason.
        """
        from xml.etree import ElementTree as ET

        payload = ET.Element("{urn:xmpp:reporting:1}report")
        if comment:
            text_el = ET.SubElement(payload, "{urn:xmpp:reporting:1}text")
            text_el.text = comment
        return payload

    # ------------------------------------------------------------------
    # Bot commands: !rtbl list / add / delete / publish
    # ------------------------------------------------------------------

    async def cmd_rtbl(self, args: list[str], room: str, actor: str = "unknown") -> None:
        """
        Manage RTBL subscriptions and the optional own publish feed.

        !rtbl list
        !rtbl add <service_jid> <node>
        !rtbl delete <service_jid> [node]
        !rtbl publish status
        !rtbl publish sync
        """
        p = self.command_prefix

        if not args:
            lines = [
                f"Usage:",
                f"  {p}rtbl list",
                f"  {p}rtbl add <service_jid> <node>",
                f"  {p}rtbl delete <service_jid> [node]",
            ]
            if getattr(self, "rtbl_publish_enabled", False):
                lines += [
                    f"  {p}rtbl publish status",
                    f"  {p}rtbl publish sync",
                ]
            self.send_message(mto=room, mbody="\n".join(lines), mtype="groupchat")
            return

        action = args[0].lower()

        # ----------------------------------------------------------------
        # list
        # ----------------------------------------------------------------
        if action == "list":
            lines = ["🛡️ RTBL Subscriptions:"]

            if not self.rtbl_subscriptions:
                lines.append("  (none)")
            else:
                for service_jid, node in self.rtbl_subscriptions:
                    async with self.db.execute(
                        "SELECT COUNT(*) FROM rtbl_hashes WHERE service_jid = ? AND node = ?",
                        (service_jid, node),
                    ) as cursor:
                        row = await cursor.fetchone()
                        h_count = row[0] if row else 0
                    async with self.db.execute(
                        "SELECT COUNT(*) FROM rtbl_domains WHERE service_jid = ? AND node = ?",
                        (service_jid, node),
                    ) as cursor:
                        row = await cursor.fetchone()
                        d_count = row[0] if row else 0
                    lines.append(
                        f"  • {service_jid}  /  {node}"
                        f"  ({h_count} hashes, {d_count} domains)"
                    )

            if getattr(self, "rtbl_publish_enabled", False):
                lines += [
                    "",
                    "📡 Own publish feed:",
                    f"  • JID hashes  → {self.rtbl_publish_service} / {self.rtbl_publish_jid_node}",
                    f"  • Domain bans → {self.rtbl_publish_service} / {self.rtbl_publish_domain_node}",
                ]

            self.send_message(mto=room, mbody="\n".join(lines), mtype="groupchat")
            return

        # ----------------------------------------------------------------
        # add
        # ----------------------------------------------------------------
        if action == "add":
            if len(args) < 3:
                self.send_message(
                    mto=room,
                    mbody=f"❌ Usage: {p}rtbl add <service_jid> <node>",
                    mtype="groupchat",
                )
                return

            service_jid = args[1].strip().lower()
            node        = args[2].strip()

            if (service_jid, node) in self.rtbl_subscriptions:
                self.send_message(
                    mto=room,
                    mbody=f"⚠️ RTBL: Already subscribed to '{node}' @ {service_jid}.",
                    mtype="groupchat",
                )
                return

            await self.db.execute(
                "INSERT OR IGNORE INTO rtbl_subscriptions (service_jid, node) VALUES (?, ?)",
                (service_jid, node),
            )
            await self.db.commit()
            await self._load_rtbl_subscriptions_from_db()

            self.send_message(
                mto=room,
                mbody=(
                    f"✅ RTBL: Added subscription to '{node}' @ {service_jid}. "
                    f"Fetching items…"
                ),
                mtype="groupchat",
            )
            await self._rtbl_subscribe_and_fetch(service_jid, node)
            self.log_event(
                logging.INFO, "rtbl_subscription_added",
                actor=actor, target_type="rtbl", target=f"{service_jid}/{node}",
            )
            await self.audit_event(
                "rtbl_subscription_added",
                actor=actor,
                target_type="rtbl",
                target=f"{service_jid}/{node}",
                comment=f"Subscribed to node '{node}' @ {service_jid}",
            )
            return

        # ----------------------------------------------------------------
        # delete
        # ----------------------------------------------------------------
        if action in ("delete", "del", "remove"):
            if len(args) < 2:
                self.send_message(
                    mto=room,
                    mbody=f"❌ Usage: {p}rtbl delete <service_jid> [node]",
                    mtype="groupchat",
                )
                return

            service_jid = args[1].strip().lower()
            node        = args[2].strip() if len(args) >= 3 else None

            if node:
                await self.db.execute(
                    "DELETE FROM rtbl_subscriptions WHERE service_jid = ? AND node = ?",
                    (service_jid, node),
                )
                await self.db.execute(
                    "DELETE FROM rtbl_hashes WHERE service_jid = ? AND node = ?",
                    (service_jid, node),
                )
                await self.db.execute(
                    "DELETE FROM rtbl_domains WHERE service_jid = ? AND node = ?",
                    (service_jid, node),
                )
                label = f"'{node}' @ {service_jid}"
            else:
                await self.db.execute(
                    "DELETE FROM rtbl_subscriptions WHERE service_jid = ?", (service_jid,)
                )
                await self.db.execute(
                    "DELETE FROM rtbl_hashes WHERE service_jid = ?", (service_jid,)
                )
                await self.db.execute(
                    "DELETE FROM rtbl_domains WHERE service_jid = ?", (service_jid,)
                )
                label = f"all subscriptions @ {service_jid}"

            await self.db.commit()
            await self._load_rtbl_subscriptions_from_db()
            self.log_event(
                logging.INFO, "rtbl_subscription_removed",
                actor=actor, target_type="rtbl", target=f"{service_jid}/{node or '*'}",
            )
            await self.audit_event(
                "rtbl_subscription_removed",
                actor=actor,
                target_type="rtbl",
                target=f"{service_jid}/{node or '*'}",
                comment=f"Removed {label}",
            )

            self.send_message(
                mto=room,
                mbody=f"✅ RTBL: Removed {label}. All hashes and domains purged from DB.",
                mtype="groupchat",
            )
            return

        # ----------------------------------------------------------------
        # publish
        # ----------------------------------------------------------------
        if action == "publish":
            if not getattr(self, "rtbl_publish_enabled", False):
                self.send_message(
                    mto=room,
                    mbody=(
                        "❌ RTBL publish is disabled. "
                        "Set RTBL_PUBLISH_ENABLED = True in config.py and restart."
                    ),
                    mtype="groupchat",
                )
                return

            sub_action = args[1].lower() if len(args) >= 2 else "status"

            if sub_action == "status":
                async with self.db.execute(
                    """SELECT COUNT(*) FROM bans
                       WHERE target_type = 'jid' AND jid IS NOT NULL
                         AND (until = 0 OR until > strftime('%s','now'))"""
                ) as cursor:
                    row = await cursor.fetchone()
                    jid_count = row[0] if row else 0

                async with self.db.execute(
                    """SELECT COUNT(*) FROM bans
                       WHERE target_type = 'domain'
                         AND (until = 0 OR until > strftime('%s','now'))"""
                ) as cursor:
                    row = await cursor.fetchone()
                    domain_count = row[0] if row else 0

                self.send_message(
                    mto=room,
                    mbody=(
                        f"📡 RTBL Publish status:\n"
                        f"  Service:     {self.rtbl_publish_service}\n"
                        f"  JID node:    {self.rtbl_publish_jid_node}"
                        f"  ({jid_count} active bans)\n"
                        f"  Domain node: {self.rtbl_publish_domain_node}"
                        f"  ({domain_count} active bans)"
                    ),
                    mtype="groupchat",
                )
                return

            if sub_action == "sync":
                self.send_message(
                    mto=room,
                    mbody="📡 RTBL Publish: Syncing all active bans to nodes…",
                    mtype="groupchat",
                )
                jid_count, domain_count = await self._rtbl_sync_all_bans_to_nodes()
                self.send_message(
                    mto=room,
                    mbody=(
                        f"✅ RTBL Publish: Sync complete — "
                        f"{jid_count} JID hashes, {domain_count} domain bans published."
                    ),
                    mtype="groupchat",
                )
                return

            self.send_message(
                mto=room,
                mbody=f"❌ Unknown publish sub-command: {sub_action}\nAvailable: status / sync",
                mtype="groupchat",
            )
            return

        self.send_message(
            mto=room,
            mbody=(
                f"❌ Unknown RTBL action: {action}\n"
                f"Available: list / add / delete / publish"
            ),
            mtype="groupchat",
        )

    # ------------------------------------------------------------------
    # Own RTBL publish feed
    # ------------------------------------------------------------------

    async def setup_rtbl_publish(self) -> None:
        """
        Create and configure own JID and domain publish nodes, then perform
        an initial sync of all active bans.
        Called from start() after setup_rtbl().
        """
        if not getattr(self, "rtbl_publish_enabled", False):
            return

        from config import ADMIN_ROOM

        for node in (self.rtbl_publish_jid_node, self.rtbl_publish_domain_node):
            await self._rtbl_ensure_node(self.rtbl_publish_service, node)

        jid_count, domain_count = await self._rtbl_sync_all_bans_to_nodes()
        log.info(
            "RTBL Publish: Initial sync complete — %d JID hashes, %d domain bans",
            jid_count, domain_count,
        )

        if self.rtbl_announce:
            self.send_message(
                mto=ADMIN_ROOM,
                mbody=(
                    f"📡 RTBL Publish: Nodes ready on {self.rtbl_publish_service} — "
                    f"{jid_count} JID hashes ({self.rtbl_publish_jid_node}), "
                    f"{domain_count} domain bans ({self.rtbl_publish_domain_node})"
                ),
                mtype="groupchat",
            )


    async def _rtbl_ensure_node(self, service: str, node: str) -> None:
        """Create a PubSub node if it does not already exist, then configure it."""
        try:
            await self.plugin["xep_0060"].create_node(service, node)
            log.info("RTBL Publish: Created node '%s' on %s", node, service)
        except IqError as e:
            if "conflict" in str(e).lower():
                log.info("RTBL Publish: Node '%s' already exists on %s", node, service)
            else:
                log.warning("RTBL Publish: Could not create node '%s': %s", node, e)
                return
        except IqTimeout as e:
            log.warning("RTBL Publish: Timeout creating node '%s': %s", node, e)
            return

        try:
            await self.plugin["xep_0060"].set_node_config(
                service,
                node,
                config={
                    "pubsub#access_model":             "open",
                    "pubsub#publish_model":            "publishers",
                    "pubsub#persist_items":            True,
                    "pubsub#max_items":                "256",
                    "pubsub#send_last_published_item": "never",
                    "pubsub#deliver_payloads":         True,
                },
            )
            log.info("RTBL Publish: Node '%s' configured", node)
        except (IqError, IqTimeout) as e:
            log.warning("RTBL Publish: Could not configure node '%s': %s", node, e)


    async def _rtbl_sync_all_bans_to_nodes(self) -> tuple[int, int]:
        """
        Publish all active JID bans and domain bans from the main bans table
        to the respective own publish nodes.
        Returns (jid_count, domain_count).
        """
        if not getattr(self, "rtbl_publish_enabled", False):
            return 0, 0

        jid_count    = 0
        domain_count = 0

        async with self.db.execute(
            """SELECT jid, comment FROM bans
               WHERE target_type = 'jid' AND jid IS NOT NULL
                 AND (until = 0 OR until > strftime('%s','now'))"""
        ) as cursor:
            jid_rows = await cursor.fetchall()

        for jid, comment in jid_rows:
            bare = self.bare_jid(jid) if jid else None
            if not bare or bare.startswith("*."):
                continue
            await self._rtbl_publish_jid_item(bare, comment)
            jid_count += 1

        async with self.db.execute(
            """SELECT target, comment FROM bans
               WHERE target_type = 'domain'
                 AND (until = 0 OR until > strftime('%s','now'))"""
        ) as cursor:
            domain_rows = await cursor.fetchall()

        for domain, comment in domain_rows:
            if not domain:
                continue
            await self._rtbl_publish_domain_item(domain, comment)
            domain_count += 1

        return jid_count, domain_count


    async def rtbl_publish_ban(
        self,
        jid: str | None,
        domain: str | None,
        comment: str | None,
    ) -> None:
        """
        Publish a single ban to the appropriate own RTBL node.

        Called from ban_all() after upsert_ban_db() succeeds.
          jid    -> hashed and published to the JID node
          domain -> published as plaintext to the domain node
        """
        if not getattr(self, "rtbl_publish_enabled", False):
            return

        if jid:
            bare = self.bare_jid(jid)
            if bare and not bare.startswith("*."):
                await self._rtbl_publish_jid_item(bare, comment)

        if domain:
            clean = domain.lstrip("*.")
            if clean:
                await self._rtbl_publish_domain_item(clean, comment)


    async def rtbl_retract_ban(
        self,
        jid: str | None,
        domain: str | None,
    ) -> None:
        """
        Retract a ban from the appropriate own RTBL node.

        Called from unban_all() after the ban has been removed from DB.
        """
        if not getattr(self, "rtbl_publish_enabled", False):
            return

        if jid:
            bare = self.bare_jid(jid)
            if bare and not bare.startswith("*."):
                hash_val = self._rtbl_hash_jid(bare)
                await self._rtbl_retract_item(self.rtbl_publish_jid_node, hash_val)

        if domain:
            clean = domain.lstrip("*.")
            if clean:
                await self._rtbl_retract_item(self.rtbl_publish_domain_node, clean)


    async def _rtbl_publish_jid_item(self, bare_jid: str, comment: str | None) -> None:
        """Publish a single SHA-256 hashed JID to the own JID pubsub node."""
        hash_val = self._rtbl_hash_jid(bare_jid)
        try:
            await self.plugin["xep_0060"].publish(
                self.rtbl_publish_service,
                self.rtbl_publish_jid_node,
                id=hash_val,
                payload=self._rtbl_build_payload(comment),
            )
            log.debug(
                "RTBL Publish: JID hash published for %s (%s…)",
                bare_jid, hash_val[:12],
            )
        except (IqError, IqTimeout) as e:
            log.warning("RTBL Publish: Could not publish JID hash for %s: %s", bare_jid, e)


    async def _rtbl_publish_domain_item(self, domain: str, comment: str | None) -> None:
        """Publish a plaintext domain ban to the own domain pubsub node."""
        try:
            await self.plugin["xep_0060"].publish(
                self.rtbl_publish_service,
                self.rtbl_publish_domain_node,
                id=domain,
                payload=self._rtbl_build_payload(comment),
            )
            log.debug("RTBL Publish: Domain ban published for *.%s", domain)
        except (IqError, IqTimeout) as e:
            log.warning("RTBL Publish: Could not publish domain ban for *.%s: %s", domain, e)


    async def _rtbl_retract_item(self, node: str, item_id: str) -> None:
        """Retract a single item from one of the own publish nodes."""
        try:
            await self.plugin["xep_0060"].retract(
                self.rtbl_publish_service,
                node,
                id=item_id,
                notify=True,
            )
            log.debug(
                "RTBL Publish: Retracted '%s…' from node '%s'",
                item_id[:16], node,
            )
        except (IqError, IqTimeout) as e:
            log.warning(
                "RTBL Publish: Could not retract '%s…' from '%s': %s",
                item_id[:16], node, e,
            )


    async def _rtbl_refresh_worker(self) -> None:
        """
        Periodically re-fetch all items from every subscribed RTBL node.
        Interval is controlled by RTBL_REFRESH_INTERVAL (seconds, 0 = disabled).
        Acts as a fallback for missed PubSub events.
        """
        while True:
            interval = getattr(self, "rtbl_refresh_interval", 3600)
            if interval <= 0:
                await asyncio.sleep(60)  # check again in 60s in case config changes
                continue

            await asyncio.sleep(interval)

            if not getattr(self, "rtbl_enabled", False):
                continue

            log.info("RTBL: Starting periodic refresh (%ds interval)", interval)
            for service_jid, node in list(self.rtbl_subscriptions):
                try:
                    await self._rtbl_fetch_all_items(service_jid, node)
                except Exception as e:
                    log.warning(
                        "RTBL: Periodic refresh failed for '%s' @ %s: %s",
                        node, service_jid, e,
                    )
            log.info("RTBL: Periodic refresh complete")

# ------------------------------------------------------------------
# Module-level helpers (not part of the mixin)
# ------------------------------------------------------------------

def _domain_matches(user_domain: str, banned_domain: str) -> bool:
    """
    Return True if user_domain is equal to banned_domain or is a subdomain of it.

    Examples:
      _domain_matches('spam.example.org', 'example.org')  -> True
      _domain_matches('example.org',      'example.org')  -> True
      _domain_matches('other.org',        'example.org')  -> False
    """
    user_domain   = user_domain.lower()
    banned_domain = banned_domain.lower()
    return user_domain == banned_domain or user_domain.endswith("." + banned_domain)
