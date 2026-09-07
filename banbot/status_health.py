"""Passive health snapshot used by the BanBot status command."""

from __future__ import annotations

import logging
from collections.abc import Iterable
from typing import Any

from envs_xmpp_core.runtime.health import (
    HealthCheck,
    HealthSnapshot,
    HealthStatus,
    analyze_task_snapshot,
    collect_health_snapshot,
    watchdog_health_state,
)

import config

log = logging.getLogger(__name__)


def _message_check(
    key: str,
    summary: str,
    *,
    problems: Iterable[str] = (),
    warnings: Iterable[str] = (),
    notes: Iterable[str] = (),
    data: dict[str, Any] | None = None,
) -> HealthCheck:
    problem_items = tuple(problems)
    warning_items = tuple(warnings)
    note_items = tuple(notes)
    status: HealthStatus
    if problem_items:
        status = "error"
    elif warning_items:
        status = "warning"
    else:
        status = "ok"
    details = dict(data or {})
    details.update(
        problems=problem_items,
        warnings=warning_items,
        notes=note_items,
    )
    return HealthCheck(key, status, summary, details)


def _connection_check(bot: Any) -> HealthCheck:
    warnings = ("Reconnect/resync is currently in progress",) if getattr(bot, "reconnecting", False) else ()
    return _message_check(
        "connection",
        "reconnect in progress" if warnings else "connected",
        warnings=warnings,
    )


def _database_available_check(bot: Any) -> HealthCheck:
    problems = ("Database connection is not available",) if not getattr(bot, "db", None) else ()
    return _message_check(
        "database",
        "database unavailable" if problems else "database available",
        problems=problems,
    )


def _tasks_check(bot: Any) -> HealthCheck:
    problems: list[str] = []
    warnings: list[str] = []
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
            bool(getattr(bot, "rtbl_enabled", False) and getattr(bot, "rtbl_refresh_interval", 0) > 0),
        ),
    ]
    for task_name, task, should_run in task_checks:
        if should_run and task is not None and task.done():
            problems.append(f"{task_name} stopped unexpectedly")

    supervisor = getattr(bot, "tasks", None)
    if supervisor is not None:
        diagnostics = analyze_task_snapshot(supervisor.snapshot(include_done=False))
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


def _watchdog_check(bot: Any) -> HealthCheck:
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


def _rooms_check(bot: Any) -> HealthCheck:
    problems: list[str] = []
    warnings: list[str] = []
    notes: list[str] = []
    protected_rooms = sorted(getattr(bot, "protected_rooms", set()))
    if not protected_rooms:
        warnings.append("No protected rooms configured\n   The bot is running but has no rooms to protect.")

    admin_state = getattr(bot, "bot_admin_state", {})
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

    admin_infos = getattr(bot, "occupants", {}).get(config.ADMIN_ROOM, {})
    admins = sorted(
        {
            bot.safe_jid(bot.bare_jid(info.get("jid")) or "unknown")
            for info in admin_infos.values()
            if info.get("affiliation") in ("owner", "admin")
        }
    )
    if not admins:
        warnings.append(
            "No admins/owners detected in the admin room\n   Admin authorization may fail until occupants are synced."
        )

    fallback_rooms: set[str] = set(getattr(bot, "admin_affiliation_query_forbidden_rooms", set()))
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


def _rtbl_check(bot: Any) -> HealthCheck:
    warnings = (
        ("RTBL is enabled but no subscriptions are configured",)
        if getattr(bot, "rtbl_enabled", False) and not getattr(bot, "rtbl_subscriptions", [])
        else ()
    )
    return _message_check(
        "rtbl",
        "RTBL configuration checked",
        warnings=warnings,
    )


async def _database_stats_check(bot: Any) -> HealthCheck:
    problems: list[str] = []
    warnings: list[str] = []
    try:
        db_stats = dict(await bot.get_db_stats() or {})
    except Exception as exc:  # noqa: BLE001 - diagnostics isolation boundary
        db_stats = {}
        problems.append(f"Database stats failed: {exc}")
        log.warning("Could not get database stats: %s", exc)

    expired_ban_rows = int(db_stats.get("expired_ban_rows", 0) or 0)
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


async def collect_status_health_snapshot(bot: Any) -> HealthSnapshot:
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
    problems: list[str] = []
    warnings: list[str] = []
    notes: list[str] = []
    for check in snapshot.checks.values():
        check_problems = tuple(check.data.get("problems", ()))
        problems.extend(str(item) for item in check_problems)
        warnings.extend(str(item) for item in check.data.get("warnings", ()))
        notes.extend(str(item) for item in check.data.get("notes", ()))
        if check.status == "error" and not check_problems and check.error:
            problems.append(f"{check.key} health check failed: {check.error}")
    return problems, warnings, notes
