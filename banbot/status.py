"""Status command output."""

import asyncio
import logging
import os
import sys
import time
from importlib import metadata
from typing import TYPE_CHECKING, cast

import psutil
from envs_xmpp_core import __version__ as envs_xmpp_version
from envs_xmpp_core.formatting import format_bytes, format_relative_time
from envs_xmpp_core.presentation import (
    RoomListRequest,
    RoomView,
    StatusSection,
    TaskListRequest,
    filter_room_views,
    filter_task_views,
    normalize_tasks,
    render_room_entry,
    render_session_lifecycle_lines,
    render_status_sections,
    render_task_entry,
    render_task_summary,
    room_summary,
)
from envs_xmpp_core.xmpp.occupants import occupant_is_admin_or_owner

import config

from ._version import __version__
from .occupants import BotOccupantMixin
from .protections.definitions import PROTECTION_DEFAULTS, PROTECTION_ORDER
from .protections.presentation import protection_status_line
from .status_health import collect_status_health_snapshot, status_health_messages
from .utils import human_time

if TYPE_CHECKING:
    from .contracts import StatusMixinHost

    class _StatusMixinContract(StatusMixinHost):
        pass
else:
    class _StatusMixinContract:
        pass

log = logging.getLogger(__name__)


def _status_int(value: object, default: int = 0) -> int:
    """Return a diagnostics integer without letting malformed state break status."""
    if not isinstance(value, (str, bytes, bytearray, int, float)):
        return default
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return default


def _package_version(package: str) -> str:
    try:
        return metadata.version(package)
    except metadata.PackageNotFoundError:
        return "unknown"


class StatusMixin(_StatusMixinContract):
    @staticmethod
    def human_size(num_bytes: int) -> str:
        return format_bytes(num_bytes, negative_label=None, max_unit="GiB")

    def _status_room_views(self, protected_rooms: list[str]) -> list[RoomView]:
        views: list[RoomView] = []
        for room_name in sorted(protected_rooms, key=str.casefold):
            bot_nick, info = BotOccupantMixin._bot_occupant_entry(cast(BotOccupantMixin, self), room_name)
            affiliation = str((info or {}).get("affiliation") or "unknown").lower()
            role = str((info or {}).get("role") or "") or None
            joined = info is not None
            is_admin = bool(info and occupant_is_admin_or_owner(info))
            details = [
                "joined" if joined else "not joined",
                "protected",
                f"affiliation={affiliation}",
            ]
            if bot_nick:
                details.append(f"nick={bot_nick}")
            if role:
                details.append(f"role={role}")
            if joined and not is_admin:
                details.append("no admin rights")
            views.append(
                RoomView(
                    jid=room_name,
                    joined=joined,
                    details=tuple(details),
                    attention=joined and not is_admin,
                    unavailable=not joined,
                )
            )
        return views

    async def _redaction_counts(self) -> tuple[int, int]:
        try:
            if hasattr(self, "flush_redaction_index"):
                await self.flush_redaction_index()
            db = self.db
            if db is None:
                return 0, 0
            async with db.execute(
                """
                SELECT COUNT(*),
                       COALESCE(SUM(CASE WHEN redacted_at IS NOT NULL THEN 1 ELSE 0 END), 0)
                FROM redaction_index
                """
            ) as cursor:
                row = await cursor.fetchone()
            return (int(row[0] or 0), int(row[1] or 0)) if row else (0, 0)
        except Exception as exc:
            log.debug("Could not get redaction index stats: %s", exc)
            return 0, 0

    async def _cmd_status(self, room: str, args: list[str] | None = None) -> None:
        args = list(args or [])
        if len(args) > 1 or (args and args[0].lower() not in {"full", "all", "details"}):
            await self.bot_send_message(
                mto=room,
                mbody=f"❌ Usage: {self.command_prefix}status [full]",
                mtype="groupchat",
            )
            return
        full = bool(args)
        now = int(time.time())

        health = await collect_status_health_snapshot(self)
        problems, warnings, notes = status_health_messages(health)
        rooms_health = health.check("rooms")
        protected_rooms = list(rooms_health.data.get("protected_rooms", ()))
        admins = list(rooms_health.data.get("admins", ()))
        room_views = self._status_room_views(protected_rooms)
        db_stats = dict(health.check("database_stats").data.get("stats", {}))

        if problems:
            banner = "❌ Bot is online, but problems were detected."
        elif warnings:
            banner = "⚠️ Bot is online, but attention is needed."
        else:
            banner = "✅ Bot is online and healthy."

        boundjid = getattr(self, "boundjid", None)
        core_lines = [
            f"Version: {__version__}",
        ]
        if getattr(self, "last_version_check_result", None):
            core_lines.append(f"Latest release: {self.last_version_check_result}")
        core_lines.extend(
            [
                f"JID: {boundjid or getattr(config, 'JID', 'unknown')}",
                f"Prefix: {self.command_prefix}",
                f"Uptime: {human_time(max(0, now - int(getattr(self, 'bot_start_time', now) or now)))}",
            ]
        )
        server_connect_time = getattr(self, "server_connect_time", None)
        if server_connect_time:
            core_lines.append(f"Connection uptime: {human_time(max(0, now - int(server_connect_time)))}")
        else:
            core_lines.append("Connection uptime: unknown")
        last_reconnect_time = getattr(self, "last_reconnect_time", None)
        if last_reconnect_time is not None:
            core_lines.append(f"Last reconnect: {human_time(max(0, now - int(last_reconnect_time)))} ago")

        runtime_lines = [
            f"Python: {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
            f"envs-xmpp: {envs_xmpp_version}",
            f"slixmpp: {_package_version('slixmpp')}",
        ]
        try:
            process = psutil.Process(os.getpid())
            runtime_lines.append(f"Memory: {process.memory_info().rss / 1024 / 1024:.1f} MiB")
            loop = asyncio.get_running_loop()
            cpu_percent = await loop.run_in_executor(None, process.cpu_percent, 0.1)
            runtime_lines.append(f"CPU: {cpu_percent:.1f}%")
            load1, load5, load15 = psutil.getloadavg()
            runtime_lines.append(f"Load: {load1:.2f} / {load5:.2f} / {load15:.2f}")
        except Exception as exc:
            log.debug("Could not read process metrics: %s", exc)

        connect_host = getattr(config, "CONNECT_HOST", None) or getattr(boundjid, "host", None) or "JID domain"
        connect_port = getattr(config, "CONNECT_PORT", 5222)
        connect_mode = "direct TLS" if getattr(config, "CONNECT_DIRECT_TLS", False) else "STARTTLS"
        xmpp_lines = [
            f"Connection: {connect_host}:{connect_port} ({connect_mode})",
            room_summary(room_views),
            f"Admin/owner rights: {sum(view.joined and not view.attention for view in room_views)}/{len(room_views)}",
            f"Pending invites: {len(getattr(self, 'pending_room_invites', {}) or {})}",
        ]
        session_lifecycle = getattr(self, "session_lifecycle", None)
        session_snapshot = getattr(session_lifecycle, "snapshot", None)
        if callable(session_snapshot):
            xmpp_lines.extend(
                render_session_lifecycle_lines(session_snapshot(), full=full)
            )
        admin_sync_at = getattr(self, "last_admin_sync_at", None)
        admin_sync_ok = getattr(self, "last_admin_sync_ok", None)
        if admin_sync_at is not None:
            state = "✅ OK" if admin_sync_ok else "⚠️ failed"
            xmpp_lines.append(
                f"Admin sync: {state} · {format_relative_time(admin_sync_at)}"
            )
            admin_sync_error = getattr(self, "last_admin_sync_error", None)
            if full and admin_sync_error:
                xmpp_lines.append(f"Admin sync error: {admin_sync_error}")

        protection_configs = getattr(self, "protections", {}) or {}
        enabled = observe = disabled = 0
        for name in PROTECTION_ORDER:
            cfg = protection_configs.get(name, PROTECTION_DEFAULTS[name])
            if cfg.get("enabled"):
                enabled += 1
                if cfg.get("observe", False):
                    observe += 1
            else:
                disabled += 1
        permanent_bans = _status_int(db_stats.get("permanent_bans", 0) or 0)
        temporary_bans = _status_int(db_stats.get("temporary_bans", 0) or 0)
        moderation_lines = [
            f"Bans: {permanent_bans} permanent · {temporary_bans} temporary",
            f"Pending auto-unban: {_status_int(db_stats.get('expired_ban_rows', 0) or 0)}",
            f"Protections: {enabled} enabled · {observe} observe · {disabled} disabled",
        ]
        if getattr(self, "rtbl_enabled", False):
            moderation_lines.append(
                "RTBL: "
                f"{len(getattr(self, 'rtbl_hash_cache', {}))} JIDs · "
                f"{len(getattr(self, 'rtbl_domain_cache', {}))} domains · "
                f"{len(getattr(self, 'rtbl_subscriptions', []))} subscriptions"
            )

        rtbl_publish_runtime_enabled = getattr(self, "rtbl_publish_enabled", False)
        rtbl_publish_config_enabled = getattr(
            self,
            "rtbl_publish_config_enabled",
            rtbl_publish_runtime_enabled or bool(getattr(self, "rtbl_publish_disabled_reason", None)),
        )
        if rtbl_publish_config_enabled or rtbl_publish_runtime_enabled:
            if rtbl_publish_runtime_enabled:
                moderation_lines.append("RTBL Publish: enabled")
                if getattr(self, "rtbl_publish_sanity_check_ok", None) is True:
                    moderation_lines.append("Sanity Check: ✅ OK")
            else:
                moderation_lines.append("RTBL Publish: ⚠️ disabled at runtime (configured: enabled)")
                if getattr(self, "rtbl_publish_disabled_reason", None):
                    moderation_lines.append(f"RTBL Publish reason: {self.rtbl_publish_disabled_reason}")

        redaction_total, redaction_redacted = await self._redaction_counts()
        database_lines = [
            f"Status: {'connected' if getattr(self, 'db', None) is not None else 'disconnected'}",
            f"Path: {getattr(config, 'DB_FILE', 'unknown')}",
            f"Size: {self.human_size(_status_int(db_stats.get('db_size_bytes', 0) or 0))}",
            f"Audit events: {_status_int(db_stats.get('audit_events', 0) or 0)} (retention: {self.audit_log_retention_days}d)",
        ]
        if getattr(self, "redaction_enabled", False) or redaction_total:
            database_lines.append(f"Redaction index: {redaction_total} tracked · {redaction_redacted} redacted")
        if getattr(self, "last_database_backup_file", None):
            database_lines.append("Backup: available")
        if getattr(self, "last_database_restore_file", None):
            database_lines.append("Last restore: recorded")

        outbox = await self.outbox_runtime_state() if hasattr(self, "outbox_runtime_state") else {}
        task_infos = list(getattr(getattr(self, "tasks", None), "snapshot", lambda **_: [])(include_done=True))
        stale_ids: set[tuple[str, str]] = set()
        task_supervisor = getattr(self, "tasks", None)
        stale_getter = getattr(task_supervisor, "stale_services", None)
        if callable(stale_getter):
            try:
                options = getattr(task_supervisor, "options", None)
                stale_after = float(getattr(options, "stale_after", 3600.0) or 3600.0)
                stale_ids = {(item.group, item.name) for item in stale_getter(stale_after)}
            except Exception:
                stale_ids = set()
        task_views = normalize_tasks(task_infos, stale_ids=stale_ids)
        task_summary = render_task_summary(task_views, tree=False, include_scopes=False)
        health_lines = [
            f"Overall: {'✅ OK' if not problems and not warnings else ('❌ problems' if problems else '⚠️ attention')}",
            *task_summary[1:4],
            f"Outbox: {_status_int(outbox.get('pending', 0))} pending · {_status_int(outbox.get('dead', 0))} dead",
        ]
        health_lines.extend(f"Problem: {item}" for item in problems)
        health_lines.extend(f"Warning: {item}" for item in warnings)
        if full:
            health_lines.extend(f"Note: {item}" for item in notes)

        sections = [
            StatusSection.from_lines("Core", core_lines, icon="⚙️"),
            StatusSection.from_lines("Runtime", runtime_lines, icon="🖥️"),
            StatusSection.from_lines("XMPP", xmpp_lines, icon="💬"),
            StatusSection.from_lines("Moderation", moderation_lines, icon="🛡️"),
            StatusSection.from_lines("Database", database_lines, icon="🗄️"),
            StatusSection.from_lines("Health", health_lines, icon="🩺"),
        ]

        if full:
            attention_lines = [*(f"❌ {item}" for item in problems), *(f"⚠️ {item}" for item in warnings)]
            attention_lines.extend(f"ℹ️ {item}" for item in notes)
            if attention_lines:
                sections.append(StatusSection.from_lines("Attention", attention_lines, icon="🚨"))

            room_problems = filter_room_views(room_views, RoomListRequest(filter="problems"))
            if room_problems:
                rendered_rooms = [render_room_entry(view) for view in room_problems[:10]]
                remaining = len(room_problems) - len(rendered_rooms)
                if remaining:
                    rendered_rooms.append(f"… {remaining} more; see {self.command_prefix}room list problems all")
                sections.append(StatusSection.from_lines("Room issues", rendered_rooms, icon="🏠"))

            problem_tasks = filter_task_views(task_views, TaskListRequest(mode="problems"))
            task_lines = render_task_summary(task_views, tree=False, include_scopes=False)
            if problem_tasks:
                task_lines.extend(["Attention:", *(render_task_entry(view) for view in problem_tasks)])
            else:
                task_lines.append(f"Complete inventory: {self.command_prefix}tasks all")
            sections.append(StatusSection.from_lines("Background tasks", task_lines, icon="🧵"))

            sections.append(
                StatusSection.from_lines(
                    "Protections",
                    [
                        protection_status_line(name, protection_configs.get(name, PROTECTION_DEFAULTS[name]))
                        for name in PROTECTION_ORDER
                    ],
                    icon="🛡️",
                )
            )
            sections.append(StatusSection.from_lines("Admins/Owners", admins or ["none found"], icon="👥"))

            operational: list[str] = []
            if getattr(self, "last_database_backup_file", None):
                operational.append(f"Last DB Backup: {self.last_database_backup_file}")
            if getattr(self, "last_database_restore_file", None):
                operational.append(f"Last DB Restore: {self.last_database_restore_file}")
            if self.admin_affiliation_query_forbidden_rooms:
                operational.append(f"Admin protection fallback rooms: {len(self.admin_affiliation_query_forbidden_rooms)}")
            if rtbl_publish_config_enabled or rtbl_publish_runtime_enabled:
                if rtbl_publish_runtime_enabled:
                    operational.extend(
                        [
                            "RTBL Publish: enabled",
                            f"Service: {self.rtbl_publish_service}",
                            f"JID node: {self.rtbl_publish_jid_node}",
                            f"Domain node: {self.rtbl_publish_domain_node}",
                        ]
                    )
                    if getattr(self, "rtbl_publish_sanity_check_ok", None) is True:
                        operational.append("Sanity Check: ✅ OK")
                elif getattr(self, "rtbl_publish_disabled_reason", None):
                    operational.append(f"RTBL Publish reason: {self.rtbl_publish_disabled_reason}")
            if operational:
                sections.append(StatusSection.from_lines("Operational details", operational, icon="🔧"))

        body = "\n".join(render_status_sections("🤖 muc_banbot Status", sections, preamble=[banner]))
        await self.bot_send_message(mto=room, mbody=body, mtype="groupchat")
