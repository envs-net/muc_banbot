"""Passive health snapshot used by the BanBot status command."""

from __future__ import annotations

import logging
from collections.abc import Iterable
from typing import TYPE_CHECKING, Any

from envs_xmpp_core.runtime.health import (
    HealthCheck,
    HealthSnapshot,
    collect_health_snapshot,
    health_check_from_messages,
    health_snapshot_messages,
    supervisor_task_health_state,
    watchdog_health_state,
)
from envs_xmpp_core.xmpp.occupants import occupant_is_admin_or_owner

import config

if TYPE_CHECKING:
    from .contracts import StatusHealthHost

log = logging.getLogger(__name__)


def _diagnostic_int(value: object, default: int = 0) -> int:
    """Coerce an operator-facing counter without failing the health snapshot."""
    if not isinstance(value, (str, bytes, bytearray, int, float)):
        return default
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return default


def _message_check(
    key: str,
    summary: str,
    *,
    problems: Iterable[str] = (),
    warnings: Iterable[str] = (),
    notes: Iterable[str] = (),
    data: dict[str, Any] | None = None,
) -> HealthCheck:
    """Compatibility seam around the shared message-based health builder."""
    return health_check_from_messages(
        key,
        summary,
        problems=problems,
        warnings=warnings,
        notes=notes,
        data=data,
    )


def _connection_check(bot: StatusHealthHost) -> HealthCheck:
    warnings = ("Reconnect/resync is currently in progress",) if bot.reconnecting else ()
    return _message_check(
        "connection",
        "reconnect in progress" if warnings else "connected",
        warnings=warnings,
    )


def _database_available_check(bot: StatusHealthHost) -> HealthCheck:
    problems = ("Database connection is not available",) if not bot.db else ()
    return _message_check(
        "database",
        "database unavailable" if problems else "database available",
        problems=problems,
    )


def _tasks_check(bot: StatusHealthHost) -> HealthCheck:
    problems: list[str] = []
    warnings: list[str] = []
    session_workers_expected = bool(getattr(bot, "_startup_completed_once", False)) and not (
        bot.reconnecting or bool(getattr(bot, "_shutdown_in_progress", False))
    )
    task_checks = [
        ("unban worker", getattr(bot, "unban_task", None), True),
        ("health check worker", getattr(bot, "health_check_task", None), True),
        (
            "version check worker",
            getattr(bot, "version_check_task", None),
            bool(getattr(bot, "version_check_enabled", False) and getattr(bot, "version_check_url", None)),
        ),
        (
            "RTBL refresh worker",
            getattr(bot, "_rtbl_refresh_task", None),
            bool(bot.rtbl_enabled and getattr(bot, "rtbl_refresh_interval", 0) > 0),
        ),
    ]
    for task_name, task, should_run in task_checks:
        if not should_run:
            continue
        if task is None:
            if session_workers_expected:
                problems.append(f"{task_name} is not running")
            continue
        if task.done() and session_workers_expected:
            problems.append(f"{task_name} stopped unexpectedly")

    supervisor = bot.tasks
    diagnostics = supervisor_task_health_state(supervisor, include_done=False)
    if diagnostics is not None:
        if diagnostics.failed_tasks:
            preview = ", ".join(info.name for info in diagnostics.failed_tasks[:5])
            problems.append(f"Supervised background task failure(s): {preview}")
        if diagnostics.restarting_tasks:
            preview = ", ".join(info.name for info in diagnostics.restarting_tasks[:5])
            warnings.append(f"Background worker restart/backoff in progress: {preview}")
        if diagnostics.restarted_tasks:
            preview = ", ".join(f"{info.name}×{info.restart_count}" for info in diagnostics.restarted_tasks[:5])
            warnings.append(f"Background worker restart(s) observed: {preview}")

    return _message_check(
        "tasks",
        "background tasks checked",
        problems=problems,
        warnings=warnings,
    )


def _watchdog_check(bot: StatusHealthHost) -> HealthCheck:
    problems: list[str] = []
    warnings: list[str] = []
    notes: list[str] = []
    state = watchdog_health_state(getattr(bot, "runtime_watchdog", None))
    if state.available and state.enabled:
        if not state.worker_running:
            problems.append("Runtime watchdog is enabled but not running")
        elif state.heartbeat_suppressed:
            warnings.append(
                f"Runtime watchdog suppressed {state.heartbeat_suppressed} heartbeat(s) because of event-loop lag"
            )
        elif state.lag_warnings:
            notes.append(f"Runtime watchdog observed event-loop lag; max {state.max_lag_seconds:.3f}s")

    return _message_check(
        "watchdog",
        "runtime watchdog checked",
        problems=problems,
        warnings=warnings,
        notes=notes,
        data={"watchdog": state.as_dict()},
    )


def _rooms_check(bot: StatusHealthHost) -> HealthCheck:
    problems: list[str] = []
    warnings: list[str] = []
    notes: list[str] = []
    protected_rooms = sorted(bot.protected_rooms)
    if not protected_rooms:
        warnings.append("No protected rooms configured\n   The bot is running but has no rooms to protect.")

    admin_state = bot.bot_admin_state
    missing_admin_rooms = sorted(room_name for room_name in protected_rooms if admin_state.get(room_name) is False)
    if missing_admin_rooms:
        preview = ", ".join(missing_admin_rooms[:5])
        if len(missing_admin_rooms) > 5:
            preview += f", … +{len(missing_admin_rooms) - 5} more"
        problems.append(f"Missing admin/owner rights in: {preview}")

    unconfirmed_admin_rooms = sorted(room_name for room_name in protected_rooms if room_name not in admin_state)
    if unconfirmed_admin_rooms:
        warnings.append(
            f"Admin/owner rights not confirmed yet in {len(unconfirmed_admin_rooms)} room(s)\n"
            "   The bot may still be waiting for room presence/state."
        )

    admin_room_key = str(config.ADMIN_ROOM).casefold()
    admin_infos: dict[str, dict[str, Any]] = next(
        (
            infos
            for room_name, infos in bot.occupants.items()
            if str(room_name).casefold() == admin_room_key
        ),
        {},
    )
    admin_jids: set[str] = set()
    for info in admin_infos.values():
        if not occupant_is_admin_or_owner(info):
            continue
        admin_jid = bot.bare_jid(info.get("jid"))
        if admin_jid:
            admin_jids.add(bot.safe_jid(admin_jid))
    admins = sorted(admin_jids)
    if not admins:
        warnings.append(
            "No admins/owners detected in the admin room\n   Admin authorization may fail until occupants are synced."
        )

    fallback_rooms: set[str] = set(bot.admin_affiliation_query_forbidden_rooms)
    if fallback_rooms:
        notes.append(
            "Admin protection fallback active in "
            f"{len(fallback_rooms)} room(s)\n"
            "   Bot is admin, but not owner there; using live occupant cache "
            "because affiliation queries are owner-only."
        )

    return _message_check(
        "rooms",
        f"{len(protected_rooms)} protected room(s)",
        problems=problems,
        warnings=warnings,
        notes=notes,
        data={
            "protected_rooms": tuple(protected_rooms),
            "admins": tuple(admins),
            "missing_admin_rooms": tuple(missing_admin_rooms),
            "unconfirmed_admin_rooms": tuple(unconfirmed_admin_rooms),
        },
    )


def _rtbl_check(bot: StatusHealthHost) -> HealthCheck:
    warnings = (
        ("RTBL is enabled but no subscriptions are configured",)
        if bot.rtbl_enabled and not bot.rtbl_subscriptions
        else ()
    )
    return _message_check(
        "rtbl",
        "RTBL configuration checked",
        warnings=warnings,
    )


async def _database_stats_check(bot: StatusHealthHost) -> HealthCheck:
    problems: list[str] = []
    warnings: list[str] = []
    try:
        db_stats = dict(await bot.get_db_stats() or {})
    except Exception as exc:  # noqa: BLE001 - diagnostics isolation boundary
        db_stats = {}
        problems.append(f"Database stats failed: {exc}")
        log.warning("Could not get database stats: %s", exc)

    expired_ban_rows = _diagnostic_int(db_stats.get("expired_ban_rows", 0) or 0)
    if expired_ban_rows > 0:
        warnings.append(
            f"{expired_ban_rows} expired tempban(s) pending auto-unban\n"
            "   The unban worker should clear them on the next cycle."
        )

    return _message_check(
        "database_stats",
        "database statistics collected" if not problems else "database statistics unavailable",
        problems=problems,
        warnings=warnings,
        data={"stats": db_stats},
    )


async def collect_status_health_snapshot(bot: StatusHealthHost) -> HealthSnapshot:
    """Collect the passive checks that determine the ``!status`` headline."""
    return await collect_health_snapshot(
        [
            ("connection", lambda: _connection_check(bot)),
            ("database", lambda: _database_available_check(bot)),
            ("tasks", lambda: _tasks_check(bot)),
            ("watchdog", lambda: _watchdog_check(bot)),
            ("rooms", lambda: _rooms_check(bot)),
            ("rtbl", lambda: _rtbl_check(bot)),
            ("database_stats", lambda: _database_stats_check(bot)),
        ]
    )


def status_health_messages(
    snapshot: HealthSnapshot,
) -> tuple[list[str], list[str], list[str]]:
    """Flatten renderer-owned problem/warning/note text in check order."""
    return health_snapshot_messages(snapshot)
