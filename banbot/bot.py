"""Main BanBot class, XMPP plugin setup, lifecycle startup, and entry point."""

import asyncio
import logging
import time
from datetime import datetime

import sys
import builtins

# Allow config.py to use lowercase boolean aliases like in YAML/JSON/TOML.
builtins.true = True
builtins.false = False

try:
    import config
except Exception as exc:
    from .config_utils import format_config_import_error
    sys.stderr.write("Failed to load config.py\n" + format_config_import_error(exc) + "\n")
    raise

import aiosqlite
from slixmpp import ClientXMPP

from config import JID, PASSWORD, ADMIN_ROOM, NICK
from .config_utils import ConfigMixin, get_config_resource
from .utils import bare_jid, safe_jid
from .audit import AuditMixin
from .cache import CacheMixin
from .db import DatabaseMixin
from .import_export import ImportExportMixin
from .moderation import ModerationMixin
from .commands import CommandMixin
from .muc import MucMixin
from .sync import SyncMixin
from .vcard import VCardMixin
from .updates import UpdateMixin
from .rtbl import RtblMixin

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)


class BanBot(
    ClientXMPP,
    ConfigMixin,
    AuditMixin,
    CacheMixin,
    DatabaseMixin,
    ImportExportMixin,
    ModerationMixin,
    CommandMixin,
    MucMixin,
    SyncMixin,
    VCardMixin,
    UpdateMixin,
    RtblMixin,
):
    bare_jid = staticmethod(bare_jid)
    safe_jid = staticmethod(safe_jid)

    def __init__(self, jid: str, password: str, resource: str = None):
        """
        Initialize the bot with:
        - Connection info (jid/resource/password)
        - RAM caches for bans and admin state
        - Semaphores to avoid flooding XMPP server
        - Uptime tracking for bot and server connection
        Sets up DB, protected rooms, occupants dicts, and registers XMPP plugins.
        """
        # Combine JID with resource if provided
        if resource:
            full_jid = f"{jid}/{resource}"
        else:
            full_jid = jid

        super().__init__(full_jid, password)
        self.db: aiosqlite.Connection | None = None

        # --- Concurrency limit for MUC write operations ---
        # Prevents flooding the XMPP server with too many IQ stanzas at once
        self.muc_write_limit = getattr(config, "MUC_WRITE_SEMAPHORE", 5)
        self.muc_write_semaphore = asyncio.Semaphore(self.muc_write_limit)

        # Ban Cache: key -> (jid_bare, nick, until, issuer, comment)
        self.ban_cache: dict[str, tuple[str | None, str | None, int, str | None, str | None]] = {}

        # Ban indexes
        self.ban_index_by_jid: dict[str, tuple] = {}
        self.ban_index_by_nick: dict[str, tuple] = {}
        self.ban_index_by_domain: dict[str, list] = {}

        self.bot_admin_state: dict[str, bool] = {}
        # Rooms where the server refuses owner/admin affiliation queries.
        # In those rooms, admin protection falls back to the live occupant cache.
        self.admin_affiliation_query_forbidden_rooms: set[str] = set()
        self.occupants: dict[str, dict] = {}
        self.protected_rooms: set[str] = set()
        self.registered_rooms: set[str] = set()
        self.room_join_time: dict[str, float] = {}
        self.reconnecting = False
        self.reconnect_task: asyncio.Task | None = None
        self.health_check_task: asyncio.Task | None = None
        self.unban_task: asyncio.Task | None = None

        # --- Uptime tracking ---
        self.bot_start_time = time.time()
        self.server_connect_time = None

        # --- default config options ---
        self.command_prefix: str = "!"
        self.announce_startup: bool = True
        self.announce_sync_details: bool = True
        self.show_ban_in_muc: bool = False
        self.allow_user_cmds: bool = True
        self.health_check_interval: int = 300
        self.unban_check_interval: int = 60
        self.max_tempban_days: int = 30

        # --- public command rate limits ---
        self.public_command_rate_limit_window: int = 30
        self.public_command_rate_limit_max: int = 3
        self.public_command_rate_limit_hits: dict[tuple[str, str, str], list[float]] = {}

        # --- import backup ---
        self.last_import_backup_file: str | None = None

        # --- structured event logs and audit retention ---
        self.structured_event_logs: bool = True
        self.audit_log_enabled: bool = True
        self.audit_log_retention_days: int = 365
        self.last_audit_cleanup_count: int = 0
        self.last_audit_cleanup_run: float = 0.0

        # --- update check ---
        self.version_check_task: asyncio.Task | None = None
        self.version_check_enabled: bool = False
        self.version_check_interval: int = 3600
        self.version_check_url: str | None = None
        self.last_version_check_result: str | None = None
        self.last_update_notified_version: str | None = None

        # --- RTBL ---
        self.rtbl_enabled: bool = getattr(config, "RTBL_ENABLED", False)
        self.rtbl_announce: bool = getattr(config, "RTBL_ANNOUNCE", True)
        self.rtbl_subscriptions: list[tuple[str, str]] = []   # loaded from DB
        self.rtbl_hash_cache: dict[str, str | None] = {}      # hash → reason
        self.rtbl_domain_cache: dict[str, str | None] = {}
        self.rtbl_ignore_jids: set[str] = set()
        self.rtbl_ignore_domains: set[str] = set()
        self._rtbl_handlers_registered: bool = False
        self.rtbl_persist_bans: bool     = getattr(config, "RTBL_PERSIST_BANS", False)
        self.rtbl_refresh_interval: int  = getattr(config, "RTBL_REFRESH_INTERVAL", 3600)
        self._rtbl_refresh_task: asyncio.Task | None = None

        # --- RTBL Publish ---
        self.rtbl_publish_enabled: bool = getattr(config, "RTBL_PUBLISH_ENABLED", False)
        self.rtbl_publish_service: str = getattr(config, "RTBL_PUBLISH_SERVICE", "")
        self.rtbl_publish_jid_node: str = getattr(config, "RTBL_PUBLISH_JID_NODE", "muc_bans_sha256")
        self.rtbl_publish_domain_node: str = getattr(config, "RTBL_PUBLISH_DOMAIN_NODE", "muc_bans_domains")

        # --- apply config ---
        self.apply_runtime_config()

        # --- Register XMPP plugins ---
        self.register_plugin("xep_0030")  # Service Discovery
        self.register_plugin("xep_0045")  # Multi-User Chat
        self.register_plugin('xep_0054')  # vCard
        self.register_plugin("xep_0060")  # PubSub (RTBL)
        self.register_plugin('xep_0084')  # Modern Avatar
        self.register_plugin('xep_0153')  # vCard Avatar compatibility

        # --- Event handlers ---
        self.add_event_handler("session_start", self.start)
        self.add_event_handler("message", self.on_direct_message)
        self.add_event_handler("groupchat_message", self.on_message)
        self.add_event_handler("groupchat_presence", self.on_muc_presence)
        self.add_event_handler("disconnected", self.on_disconnect)
        self.add_event_handler("connection_failed", self.on_disconnect)


    async def stop_background_tasks(self) -> None:
        """Cancel running background tasks before starting new ones."""
        for task in (self.unban_task, self.health_check_task, self.version_check_task):
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass


    async def start(self, _) -> None:
        """
        Called when the XMPP session starts.
        - Initializes DB
        - Joins admin and protected rooms
        - Waits for occupants
        - Syncs admins
        - Applies all bans in parallel
        - Starts unban worker
        """
        await self.stop_background_tasks()

        await self.setup_db()
        await self.load_bans_from_db()
        await self.cleanup_old_audit_logs()

        if not self.reconnecting:
            # First connection only
            self.bot_start_time = time.time()
        else:
            log.info("🔄 Reconnected successfully")

        self.reconnect_task = None

        self.send_presence()
        await self.get_roster()

        await asyncio.sleep(3)

        # --- Record server connection time ---
        self.server_connect_time = time.time()

        # --- Join admin room ---
        self.plugin["xep_0045"].join_muc(ADMIN_ROOM, NICK)
        self.room_join_time[ADMIN_ROOM] = time.time()

        if ADMIN_ROOM not in self.registered_rooms:
            self.add_event_handler(f"muc::{ADMIN_ROOM}::got_online", self.muc_online)
            self.add_event_handler(f"muc::{ADMIN_ROOM}::got_offline", self.muc_offline)
            self.registered_rooms.add(ADMIN_ROOM)

        # --- Join protected rooms ---
        for room in self.protected_rooms:
            self.plugin["xep_0045"].join_muc(room, NICK)
            self.room_join_time[room] = time.time()
            if room not in self.registered_rooms:
                self.add_event_handler(f"muc::{room}::got_online", self.muc_online)
                self.add_event_handler(f"muc::{room}::got_offline", self.muc_offline)
                self.registered_rooms.add(room)

        # --- Wait for occupants to populate ---
        await self.wait_for_occupants(timeout=20)

        # --- Check admin rights in all protected rooms ---
        await self.check_bot_admin_rights()

        # --- Sync Admins ---
        await self.sync_admins(announce=False)

        # --- Apply all bans in parallel at startup ---
        await self.sync_bans_startup()

        # --- Setup RTBL subscriptions ---
        await self.setup_rtbl()

        # --- Setup RTBL Publish-Node ---
        await self.setup_rtbl_publish()

        # --- Start RTBL periodic refresh worker ---
        if self._rtbl_refresh_task:
            self._rtbl_refresh_task.cancel()
        self._rtbl_refresh_task = asyncio.create_task(self._rtbl_refresh_worker())

        # --- Start unban worker ---
        self.unban_task = asyncio.create_task(self.unban_worker())

        # --- Start health check worker ---
        self.health_check_task = asyncio.create_task(self.health_check_worker())

        # --- Start version check worker ---
        if self.version_check_enabled and self.version_check_url:
            self.version_check_task = asyncio.create_task(self.version_check_worker())

        # --- Set Bot vCard ---
        await self.update_vcard()

        self.reconnecting = False

        # Send startup notification if enabled
        if self.announce_startup:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self.send_message(
                mto=ADMIN_ROOM,
                mbody=f"✅ Bot has restarted and synced all bans. ({timestamp})",
                mtype="groupchat"
            )

        log.info("✅ Bot started, all rooms joined and bans applied")


def main() -> None:
    """Entry point for the BanBot."""
    resource = get_config_resource()
    try:
        xmpp = BanBot(JID, PASSWORD, resource)
        errors, warnings = xmpp._validate_config()
        validation_msg = xmpp._format_config_validation(errors, warnings)
        for line in validation_msg.splitlines():
            if line.startswith("❌") or (line.startswith("- ") and errors):
                log.error(line)
            elif line.startswith("⚠️") or (line.startswith("- ") and warnings):
                log.warning(line)
            else:
                log.info(line)
    except Exception as e:
        log.error("Startup config validation failed: %s", e)
        raise SystemExit(1)

    if xmpp.connect():
        log.info("Connected successfully. Starting event loop...")
        try:
            xmpp.loop.run_forever()
        except KeyboardInterrupt:
            log.info("Bot stopped manually.")
            if xmpp.db:
                xmpp.loop.run_until_complete(xmpp.db.close())
            xmpp.disconnect()
    else:
        log.error("Unable to connect to XMPP server.")
