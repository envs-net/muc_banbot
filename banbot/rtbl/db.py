"""RTBL database schema setup, subscription loading, and in-memory caches."""

import logging

log = logging.getLogger(__name__)


class RtblDatabaseMixin:
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
