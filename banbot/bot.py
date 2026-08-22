"""Main BanBot class, XMPP plugin setup, lifecycle startup, and entry point."""

import asyncio
import inspect
import logging
import signal
import sys
import time
from datetime import datetime

from .config_loader import format_config_import_error, load_config_module

try:
    config = load_config_module()
except Exception as exc:
    sys.stderr.write("Failed to load config.py\n" + format_config_import_error(exc) + "\n")
    raise SystemExit(1) from None

import aiosqlite
from slixmpp import ClientXMPP

JID = config.JID
PASSWORD = config.PASSWORD
ADMIN_ROOM = config.ADMIN_ROOM
NICK = config.NICK
from . import config as banbot_config
get_config_resource = banbot_config.get_config_resource
from .utils import bare_jid, safe_jid
from .audit import AuditMixin
from .cache import CacheMixin
from .db import DatabaseMixin
from .import_export import ImportExportMixin
from .admin import AdminMixin
from .moderation import ModerationMixin
from .commands import CommandMixin
from .direct_messages import DirectMessageMixin
from .rooms import RoomMixin
from .rooms import RoomInviteMixin
from .redaction import RedactionMixin
from .ban_queries import BanQueryMixin
from .status import StatusMixin
from .muc import MucMixin
from .health_check import HealthCheckMixin
from .sync import SyncMixin
from .vcard import VCardMixin
from .updates import UpdateMixin
from .ignorelist import IgnorelistMixin
from .rtbl import RtblMixin
from .messaging import MessagingMixin
from .omemo import OmemoMixin
from .alerts import AlertMixin
from .backups import BackupMixin
from .protections import ProtectionMixin
from .task_supervisor import TaskSupervisor
from .runtime_watchdog import RuntimeWatchdog

_log_level_name = str(getattr(config, "LOG_LEVEL", "INFO")).upper()
_log_level = getattr(logging, _log_level_name, None)

if not isinstance(_log_level, int):
    _log_level = logging.INFO

logging.basicConfig(level=_log_level)


_SLIXMPP_STATUSES_WARNING = "Unknown stanza interface: statuses"


def _is_slixmpp_statuses_warning(record: logging.LogRecord) -> bool:
    return record.getMessage() == _SLIXMPP_STATUSES_WARNING


class _SlixmppStatusesWarningFilter(logging.Filter):
    """Suppress noisy Slixmpp warnings for unknown ``statuses`` interfaces.

    Some servers/clients include status-related XML that Slixmpp may expose as
    an unknown ``statuses`` stanza interface. Slixmpp logs this exact warning,
    which can spam production logs even though BanBot does not need that
    interface.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        return not _is_slixmpp_statuses_warning(record)


def _install_slixmpp_statuses_warning_filter() -> None:
    """Install a narrow filter for Slixmpp's known ``statuses`` noise.

    Handler filters are not enough in every runtime setup: third-party logging
    configuration can replace root handlers after startup, and records emitted
    by child loggers do not run through filters attached only to ancestor
    loggers. Wrapping ``Logger.handle`` drops the single known-noisy record
    before any handler sees it, while all other warnings continue normally.
    """

    root_logger = logging.getLogger()
    already_installed = any(
        isinstance(existing_filter, _SlixmppStatusesWarningFilter)
        for existing_filter in root_logger.filters
    )
    if not already_installed:
        root_logger.addFilter(_SlixmppStatusesWarningFilter())

    for handler in root_logger.handlers:
        handler_has_filter = any(
            isinstance(existing_filter, _SlixmppStatusesWarningFilter)
            for existing_filter in handler.filters
        )
        if not handler_has_filter:
            handler.addFilter(_SlixmppStatusesWarningFilter())

    if getattr(logging, "_banbot_statuses_warning_handle_filter_installed", False):
        return

    original_handle = logging.Logger.handle

    def handle_with_statuses_filter(
        self: logging.Logger,
        record: logging.LogRecord,
    ) -> None:
        if _is_slixmpp_statuses_warning(record):
            return None
        return original_handle(self, record)

    logging.Logger.handle = handle_with_statuses_filter
    logging._banbot_statuses_warning_handle_filter_installed = True


_install_slixmpp_statuses_warning_filter()
log = logging.getLogger(__name__)


def connect_xmpp(xmpp) -> bool:
    """Connect using optional startup-only host/port/direct-TLS settings."""
    host = getattr(config, "CONNECT_HOST", None)
    port = getattr(config, "CONNECT_PORT", 5222)
    direct_tls = getattr(config, "CONNECT_DIRECT_TLS", False)

    configured_jid_domain = str(getattr(config, "JID", "")).split("@", 1)[-1].split("/", 1)[0]
    boundjid = getattr(xmpp, "boundjid", None)
    bound_host = (
        getattr(boundjid, "host", None)
        or getattr(boundjid, "domain", None)
    )
    connect_host = host or bound_host or configured_jid_domain
    connect_port = int(port)

    signature = inspect.signature(xmpp.connect)
    parameters = signature.parameters
    kwargs = {}

    # Current Slixmpp accepts address=(host, port). Some older versions used
    # host=/port=. Keep both forms compatible without passing unknown kwargs.
    if "address" in parameters:
        kwargs["address"] = (connect_host, connect_port)
    else:
        if "host" in parameters:
            kwargs["host"] = connect_host
        if "port" in parameters:
            kwargs["port"] = connect_port

    # Direct TLS / legacy SSL mode. For normal STARTTLS connections, do not
    # force STARTTLS here; let Slixmpp negotiate stream features itself.
    if "use_ssl" in parameters:
        kwargs["use_ssl"] = bool(direct_tls)
    if direct_tls and "force_starttls" in parameters:
        kwargs["force_starttls"] = False

    log.info(
        "Connecting to XMPP server %s:%s (%s)",
        connect_host,
        connect_port,
        "direct TLS" if direct_tls else "STARTTLS",
    )
    return xmpp.connect(**kwargs)


class BanBot(
    ClientXMPP,
    MessagingMixin,
    OmemoMixin,
    AlertMixin,
    BackupMixin,
    ProtectionMixin,
    AuditMixin,
    CacheMixin,
    DatabaseMixin,
    ImportExportMixin,
    AdminMixin,
    ModerationMixin,
    CommandMixin,
    DirectMessageMixin,
    RoomMixin,
    RoomInviteMixin,
    RedactionMixin,
    BanQueryMixin,
    StatusMixin,
    MucMixin,
    HealthCheckMixin,
    SyncMixin,
    VCardMixin,
    UpdateMixin,
    IgnorelistMixin,
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
        self.tasks = TaskSupervisor()
        self.runtime_watchdog = RuntimeWatchdog(self)
        self._shutdown_lock = asyncio.Lock()
        self._shutdown_complete = False
        self.init_alert_state()

        # --- Concurrency limit for MUC write operations ---
        # Prevents flooding the XMPP server with too many IQ stanzas at once
        self.muc_write_limit = getattr(config, "MUC_WRITE_SEMAPHORE", 5)
        self.muc_write_semaphore = asyncio.Semaphore(self.muc_write_limit)
        self.sync_batch_size = getattr(config, "SYNC_BATCH_SIZE", 10)
        self.list_page_size = getattr(config, "LIST_PAGE_SIZE", 10)
        self.config_output_mode = str(getattr(config, "CONFIG_OUTPUT_MODE", "all")).lower().strip()
        self.help_output_mode = str(getattr(config, "HELP_OUTPUT_MODE", "all")).lower().strip()

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
        self.room_bot_nicks: dict[str, str] = {}
        self.room_join_events: dict[str, asyncio.Event] = {}
        self.protected_rooms: set[str] = set()
        self.registered_rooms: set[str] = set()
        self.init_room_invite_state()
        self.init_protection_state()
        self.room_join_time: dict[str, float] = {}
        self.reconnecting = False
        self.last_reconnect_time: float | None = None
        self.reconnect_task: asyncio.Task | None = None
        self.reconnect_success_event: asyncio.Event | None = None
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

        # --- runtime/systemd watchdog (startup-only settings) ---
        self.watchdog_enabled: bool = bool(getattr(config, "WATCHDOG_ENABLED", True))
        self.watchdog_interval_seconds: float = float(getattr(config, "WATCHDOG_INTERVAL_SECONDS", 20))
        self.watchdog_lag_warning_seconds: float = float(getattr(config, "WATCHDOG_LAG_WARNING_SECONDS", 2.0))
        self.watchdog_lag_failure_seconds: float = float(getattr(config, "WATCHDOG_LAG_FAILURE_SECONDS", 30.0))

        # --- public command rate limits ---
        self.public_command_rate_limit_window: int = 30
        self.public_command_rate_limit_max: int = 3
        self.public_command_rate_limit_hits: dict[tuple[str, str, str], list[float]] = {}

        # --- database backups / file operations ---
        self.last_database_backup_file: str | None = None
        self.last_database_restore_file: str | None = None
        self._database_file_operation_lock = asyncio.Lock()
        self._ban_state_operation_lock = asyncio.Lock()

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
        self.previous_startup_version: str | None = None

        # --- Redaction ---
        self.redaction_enabled: bool = getattr(config, "REDACTION_ENABLED", False)
        self.redaction_index_retention_days: int = getattr(config, "REDACTION_INDEX_RETENTION_DAYS", 30)
        self.auto_redact_on_imported_ban_reason: bool = getattr(config, "AUTO_REDACT_ON_IMPORTED_BAN_REASON", False)
        self.auto_redact_on_manual_muc_ban: bool = getattr(config, "AUTO_REDACT_ON_MANUAL_MUC_BAN", True)
        self.redaction_auto_reasons: list[str] = list(getattr(config, "REDACTION_AUTO_REASONS", []))
        self.redaction_retract_concurrency: int = getattr(config, "REDACTION_RETRACT_CONCURRENCY", 10)
        self.redaction_iq_timeout_seconds: float = getattr(config, "REDACTION_IQ_TIMEOUT_SECONDS", 5)
        self.redaction_cleanup_task: asyncio.Task | None = None
        self.redaction_operation_tasks: set[asyncio.Task] = set()
        self._redaction_confirmation_waiters: dict[tuple[str, str], set[asyncio.Event]] = {}

        # --- RTBL ---
        self.rtbl_enabled: bool = getattr(config, "RTBL_ENABLED", False)
        self.rtbl_announce: bool = getattr(config, "RTBL_ANNOUNCE", True)
        self.rtbl_subscriptions: list[tuple[str, str]] = []   # loaded from DB
        self.rtbl_hash_cache: dict[str, str | None] = {}      # hash → reason
        self.rtbl_domain_cache: dict[str, str | None] = {}
        # RTBL runtime status/observability.
        # Keys are (service_jid.lower(), node).
        self.rtbl_last_fetch: dict[tuple[str, str], float] = {}
        self.rtbl_last_change: dict[tuple[str, str], float] = {}
        self.rtbl_last_error: dict[tuple[str, str], str | None] = {}
        self.rtbl_last_counts: dict[tuple[str, str], tuple[int, int]] = {}

        self.ignore_jids: set[str] = set()
        self.ignore_domains: set[str] = set()

        self._rtbl_handlers_registered: bool = False
        self.rtbl_refresh_interval: int = getattr(config, "RTBL_REFRESH_INTERVAL", 3600)
        self._rtbl_refresh_task: asyncio.Task | None = None

        # --- RTBL Publish ---
        self.rtbl_publish_config_enabled: bool = getattr(config, "RTBL_PUBLISH_ENABLED", False)
        self.rtbl_publish_enabled: bool = self.rtbl_publish_config_enabled
        self.rtbl_publish_service: str = getattr(config, "RTBL_PUBLISH_SERVICE", "")
        self.rtbl_publish_jid_node: str = getattr(config, "RTBL_PUBLISH_JID_NODE", "muc_bans_sha256")
        self.rtbl_publish_domain_node: str = getattr(config, "RTBL_PUBLISH_DOMAIN_NODE", "muc_bans_domains")
        self.rtbl_publish_sanity_check_ok: bool | None = None
        self.rtbl_publish_disabled_reason: str | None = None

        # --- apply config ---
        self.apply_runtime_config()

        # --- Register XMPP plugins ---
        self.register_plugin("xep_0030")  # Service Discovery
        self.register_plugin("xep_0045")  # Multi-User Chat
        self.register_plugin('xep_0054')  # vCard
        self.register_plugin("xep_0060")  # PubSub (RTBL)
        self.register_plugin('xep_0084')  # Modern Avatar
        self.register_plugin('xep_0153')  # vCard Avatar compatibility
        self.register_plugin("xep_0249")  # Direct MUC invites
        self.register_plugin("xep_0313")  # Message Archive Management

        # --- Optional OMEMO support ---
        self.configure_omemo()

        # --- Event handlers ---
        self.add_event_handler("session_start", self.start)

        # Inspect raw message stanzas for direct/mediated MUC invite payloads.
        # This catches invites that do not reliably trigger a Slixmpp invite event.
        self.add_event_handler("message", self.on_room_invite_message)

        # Slixmpp invite events:
        # - groupchat_invite: mediated MUC invite via XEP-0045
        # - groupchat_direct_invite: direct MUC invite via XEP-0249
        self.add_event_handler("groupchat_invite", self.on_room_invite)
        self.add_event_handler("groupchat_direct_invite", self.on_room_invite)

        # XEP-0425 moderation announcements can be bodyless and may bypass
        # higher-level message events or namespace-specific stream matchers.
        # An incoming sync filter sees every parsed stanza before handlers run.
        self.add_filter("in", self._redaction_incoming_filter)
        self.add_event_handler("message", self.on_direct_message)
        self.add_event_handler("groupchat_message", self.on_message)
        self.add_event_handler("groupchat_presence", self.on_muc_presence)

        self.add_event_handler("disconnected", self.on_disconnect)
        self.add_event_handler("connection_failed", self.on_disconnect)


    def connect_with_config(self) -> bool:
        """Connect using optional config.py host/port/direct-TLS settings."""
        return connect_xmpp(self)


    async def stop_background_tasks(self) -> None:
        """Cancel reconnect-scoped workers before starting fresh ones."""
        supervisor = getattr(self, "tasks", None)
        if supervisor is not None:
            await supervisor.cancel_group("_core")

        # Keep compatibility with lightweight tests/embedders that replace task
        # attributes without registering them in the supervisor. Short-lived
        # redaction operation tasks intentionally remain outside the service
        # supervisor and are cancelled explicitly here.
        operation_tasks = list(getattr(self, "redaction_operation_tasks", set()))
        for task in (
            self._rtbl_refresh_task,
            self.unban_task,
            self.health_check_task,
            self.version_check_task,
            self.redaction_cleanup_task,
            *operation_tasks,
        ):
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    log.debug("Background task cancelled during shutdown")

    async def shutdown(self) -> None:
        """Flush state and stop process-scoped resources exactly once."""
        async with self._shutdown_lock:
            if self._shutdown_complete:
                return

            try:
                flush_redaction_index = getattr(self, "flush_redaction_index", None)
                if callable(flush_redaction_index):
                    await flush_redaction_index()
            except Exception as exc:
                log.warning("Shutdown: failed to flush redaction index: %s", exc)

            try:
                await self.stop_background_tasks()
            except Exception as exc:
                log.warning("Shutdown: failed to stop background tasks cleanly: %s", exc)

            try:
                await self.runtime_watchdog.stop()
            except Exception as exc:
                log.warning("Shutdown: failed to stop runtime watchdog cleanly: %s", exc)

            try:
                await self.tasks.cancel_all()
            except Exception as exc:
                log.warning("Shutdown: failed to cancel supervised tasks cleanly: %s", exc)

            db = getattr(self, "db", None)
            if db is not None:
                try:
                    await db.close()
                    self.db = None
                except Exception as exc:
                    log.warning("Shutdown: failed to close database cleanly: %s", exc)

            try:
                try:
                    result = self.disconnect(wait=False)
                except TypeError:
                    result = self.disconnect()
                if inspect.isawaitable(result):
                    await result
            except Exception as exc:
                log.warning("Shutdown: failed to disconnect XMPP cleanly: %s", exc)

            self._shutdown_complete = True

    def _start_core_service(self, factory, *, name: str) -> asyncio.Task:
        """Start one resilient reconnect-scoped background service."""
        return self.tasks.create_resilient(
            "_core",
            factory,
            name=name,
            service=True,
        )


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

        was_reconnecting = bool(self.reconnecting)

        await self.setup_db()
        await self.prepare_startup_version_notice(reconnecting=was_reconnecting)
        if self.redaction_enabled and hasattr(self, "run_redaction_cleanup_automatic"):
            await self.run_redaction_cleanup_automatic(actor="system")
        await self.load_pending_room_invites()
        await self.load_bans_from_db()
        await self.cleanup_old_audit_logs()
        await self.setup_ignorelist()
        await self.load_protections()

        if not was_reconnecting:
            # First connection only
            self.bot_start_time = time.time()
        else:
            self.last_reconnect_time = time.time()
            log.info("🔄 Reconnected successfully")
            if self.reconnect_success_event is not None:
                self.reconnect_success_event.set()

        self.send_presence()
        await self.get_roster()

        await asyncio.sleep(3)

        # --- Record server connection time ---
        self.server_connect_time = time.time()

        # Register room handlers before sending join presence.  Awaited joins can
        # otherwise receive self-presence before the BanBot handlers exist.
        managed_rooms = [ADMIN_ROOM, *sorted(self.protected_rooms - {ADMIN_ROOM})]
        for room in managed_rooms:
            if room not in self.registered_rooms:
                self.add_event_handler(f"muc::{room}::got_online", self.muc_online)
                self.add_event_handler(f"muc::{room}::got_offline", self.muc_offline)
                self.registered_rooms.add(room)

        # Explicitly await/consume all Slixmpp join Futures.  Rooms are joined in
        # parallel so one slow remote MUC does not block all other rooms.
        join_results = await asyncio.gather(
            *(
                self.ensure_muc_joined(room)
                for room in managed_rooms
            )
        )
        failed_joins = [room for room, joined in zip(managed_rooms, join_results) if not joined]
        if failed_joins:
            log.warning("MUC joins failed for: %s", ", ".join(failed_joins))

        # --- Wait briefly for remaining occupant presences to populate ---
        await self.wait_for_occupants(timeout=6)

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
        self._rtbl_refresh_task = self._start_core_service(
            self._rtbl_refresh_worker,
            name="rtbl-refresh-worker",
        )

        # --- Start unban worker ---
        self.unban_task = self._start_core_service(
            self.unban_worker,
            name="unban-worker",
        )

        # The health worker is started after reconnect state is cleared below,
        # allowing its immediate first cycle to retry any failed room joins.

        # --- Start redaction cleanup worker ---
        if self.redaction_enabled and hasattr(self, "redaction_cleanup_worker"):
            self.redaction_cleanup_task = self._start_core_service(
                self.redaction_cleanup_worker,
                name="redaction-cleanup-worker",
            )

        # --- Start version check worker ---
        if self.version_check_enabled and self.version_check_url:
            self.version_check_task = self._start_core_service(
                self.version_check_worker,
                name="version-check-worker",
            )

        # Flush batched redaction-index writes from startup room history before
        # moving on to vCard/startup announcements.
        if hasattr(self, "flush_redaction_index"):
            await self.flush_redaction_index()

        # --- Set Bot vCard ---
        await self.update_vcard()

        self.reconnecting = False

        # --- Start health check worker ---
        # Its first cycle runs immediately and retries rooms whose startup join
        # did not produce confirmed self-presence.
        self.health_check_task = self._start_core_service(
            self.health_check_worker,
            name="health-check-worker",
        )

        if was_reconnecting:
            await self.send_operational_alert(
                "reconnect_success",
                "Reconnect completed",
                "BanBot reconnected successfully and synced rooms/bans.",
                enabled=getattr(self, "alert_on_reconnect", True),
            )

        missing_rooms = sorted(
            room
            for room in managed_rooms
            if self._bot_occupant_entry(room)[1] is None
        )

        # Send lifecycle notification if enabled. Do not claim that every room
        # joined when automatic recovery is still working on failed joins.
        if self.announce_startup:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            action = "reconnected" if was_reconnecting else "restarted"
            if missing_rooms:
                lifecycle_body = (
                    f"⚠️ Bot has {action}; automatic rejoin is active for "
                    f"{len(missing_rooms)} room(s): {', '.join(missing_rooms)}. "
                    f"({timestamp})"
                )
            else:
                lifecycle_body = f"✅ Bot has {action} and synced all bans. ({timestamp})"
            await self.bot_send_message(
                mto=ADMIN_ROOM,
                mbody=lifecycle_body,
                mtype="groupchat"
            )

        await self.finalize_startup_version_notice(reconnecting=was_reconnecting)

        # Type=notify units become ready only after the complete room/database
        # startup path succeeded. The watchdog task itself is process-scoped and
        # remains active across XMPP reconnects.
        await self.runtime_watchdog.start()
        self.runtime_watchdog.notify_ready()

        if missing_rooms:
            log.warning(
                "Bot started; automatic rejoin pending for: %s",
                ", ".join(missing_rooms),
            )
        else:
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

    if not connect_xmpp(xmpp):
        log.error("Unable to connect to XMPP server.")
        raise SystemExit(1)

    shutdown_signal: int | None = None
    previous_signal_handlers: dict[int, object] = {}

    def request_shutdown(signum: int, _frame) -> None:
        nonlocal shutdown_signal
        if shutdown_signal is None:
            shutdown_signal = signum
            try:
                signal_name = signal.Signals(signum).name
            except ValueError:
                signal_name = str(signum)
            log.info("Received %s; shutting down cleanly.", signal_name)
        stop = getattr(xmpp.loop, "stop", None)
        if callable(stop):
            stop()

    for signum in (signal.SIGTERM, signal.SIGINT):
        try:
            previous_signal_handlers[signum] = signal.getsignal(signum)
            signal.signal(signum, request_shutdown)
        except (OSError, RuntimeError, ValueError):
            # Embedders/tests may run the entry point outside the main thread.
            pass

    log.info("Connected successfully. Starting event loop...")
    unexpected_loop_stop = False
    try:
        xmpp.loop.run_forever()
        unexpected_loop_stop = shutdown_signal is None
    except KeyboardInterrupt:
        # Keep compatibility with environments where SIGINT still manifests as
        # KeyboardInterrupt instead of going through the installed handler.
        shutdown_signal = signal.SIGINT
        log.info("Bot stopped manually.")
    finally:
        for signum, previous in previous_signal_handlers.items():
            try:
                signal.signal(signum, previous)
            except (OSError, RuntimeError, ValueError):
                pass

        shutdown = getattr(xmpp, "shutdown", None)
        if callable(shutdown):
            xmpp.loop.run_until_complete(shutdown())
        else:
            # Compatibility for lightweight embedders/test doubles.
            db = getattr(xmpp, "db", None)
            if db is not None:
                xmpp.loop.run_until_complete(db.close())
            disconnect = getattr(xmpp, "disconnect", None)
            if callable(disconnect):
                disconnect()

    if unexpected_loop_stop:
        log.error("XMPP event loop stopped unexpectedly.")
        raise SystemExit(1)
