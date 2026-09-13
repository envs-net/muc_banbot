"""Runtime/config admin command handlers."""

import asyncio
import inspect
import logging
import os

from envs_xmpp_core.pagination import format_page
from envs_xmpp_core.presentation import (
    TaskListRequest,
    filter_task_views,
    normalize_tasks,
    parse_task_list_request,
    render_task_entry,
    render_task_summary,
    render_watchdog_lines,
)

from .._version import __version__
from ..utils import get_list_page_size
from .context import commands_module_attr

log = logging.getLogger(__name__)

SUPERVISOR_RESTART_EXIT_CODE = 75


class CommandRuntimeMixin:
    async def _dispatch_runtime_admin_command(
        self,
        room: str,
        nick: str,
        cmd: str,
        args: list[str],
    ) -> bool:
        """Handle runtime/config admin commands that do not need ban-state locking."""
        if cmd == "config":
            actor_jid = self._actor_jid_from_room_nick(room, nick)
            await self._cmd_config(room, args, actor=actor_jid)
            return True

        if cmd in ("reload", "reloadconfig"):
            await self._cmd_reloadconfig(room)
            return True

        if cmd == "restart":
            await self._cmd_restart(room, args)
            return True

        if cmd == "status":
            await self._cmd_status(room, args)
            return True

        if cmd == "tasks":
            await self._cmd_tasks(room, args)
            return True

        if cmd in ("checkupdate", "updatecheck"):
            await self._cmd_checkupdate(room)
            return True

        return False

    def _task_stale_after(self) -> float:
        supervisor = getattr(self, "tasks", None)
        options = getattr(supervisor, "options", None)
        try:
            return float(getattr(options, "stale_after", 3600.0) or 3600.0)
        except (TypeError, ValueError):
            return 3600.0

    def _task_stale_ids(self) -> set[tuple[str, str]]:
        supervisor = getattr(self, "tasks", None)
        stale_getter = getattr(supervisor, "stale_services", None)
        if not callable(stale_getter):
            return set()
        return {
            (item.group, item.name)
            for item in stale_getter(self._task_stale_after())
        }

    async def _cmd_tasks(self, room: str, args: list[str], *, mtype: str = "groupchat") -> None:
        """Show shared task health or a filtered supervised-task inventory."""
        supervisor = getattr(self, "tasks", None)
        snapshot = getattr(supervisor, "snapshot", None)
        if not callable(snapshot):
            await self.bot_send_message(
                mto=room,
                mbody="⚠️ Background task supervisor is not available.",
                mtype=mtype,
            )
            return

        request = parse_task_list_request(args or [])
        if request.error:
            await self.bot_send_message(
                mto=room,
                mbody=f"❌ {self._tasks_usage_text()}",
                mtype=mtype,
            )
            return

        infos = list(snapshot(include_done=True))
        views = normalize_tasks(infos, stale_ids=self._task_stale_ids())

        if request.mode == "overview":
            lines = ["🧵 Background Tasks", "", *render_task_summary(views)]
            problems = filter_task_views(views, TaskListRequest(mode="problems"))
            if problems:
                lines.extend(["", "⚠️ Problems"])
                lines.extend(render_task_entry(view, full=False) for view in problems[:5])
            watchdog = getattr(self, "runtime_watchdog", None)
            runtime_state = getattr(watchdog, "runtime_state", None)
            if callable(runtime_state):
                lines.extend(["", "🐕 Runtime Watchdog", *render_watchdog_lines(runtime_state())])
            await self.bot_send_message(mto=room, mbody="\n".join(lines), mtype=mtype)
            return

        filtered = filter_task_views(views, request)
        if request.mode == "show" and not filtered:
            await self.bot_send_message(
                mto=room,
                mbody=f"⚠️ Task not found: {request.show}",
                mtype=mtype,
            )
            return

        entries = [render_task_entry(view, full=request.full or request.mode == "show") for view in filtered]
        if not entries:
            entries = [
                "✅ No background tasks match this view."
                if request.mode in {"failed", "stale", "restarting", "problems"}
                else "No supervised tasks found."
            ]

        title = "🧵 Background Tasks"
        qualifiers = []
        if request.scope:
            qualifiers.append(f"scope={request.scope}")
        if request.mode not in {"inventory", "overview"}:
            qualifiers.append(request.mode)
        if request.full:
            qualifiers.append("full")
        if qualifiers:
            title += " — " + " — ".join(qualifiers)

        lines = format_page(
            title,
            entries,
            page_request=request.page,
            page_size=5 if request.full else get_list_page_size(self),
            command_hint=f"{self.command_prefix}tasks",
        )
        await self.bot_send_message(mto=room, mbody="\n".join(lines), mtype=mtype)

    async def _cmd_checkupdate(self, room: str) -> None:
        """Check for a newer release and report the result to the admin room."""
        is_update, remote_version, error_message = await self.check_for_updates_once(announce=False)

        if error_message:
            await self.bot_send_message(
                mto=room,
                mbody=f"❌ Update check failed: {error_message}",
                mtype="groupchat",
            )
        elif is_update:
            await self.bot_send_message(
                mto=room,
                mbody=(
                    f"⬆️ New bot version available: {remote_version} (current: {__version__})\n"
                    f"Release page: {self.version_check_url}"
                ),
                mtype="groupchat",
            )
        else:
            await self.bot_send_message(
                mto=room,
                mbody=f"✅ Bot is up to date ({__version__})",
                mtype="groupchat",
            )

    async def _cmd_restart(self, room: str, args: list[str]) -> None:
        """Admin command to exit cleanly so a supervisor such as systemd can restart the bot."""
        p = self.command_prefix

        if not args or args[0].lower() != "confirm":
            await self.bot_send_message(
                mto=room,
                mbody=(
                    "⚠️ This will stop the bot process. "
                    "If it is managed by systemd or another supervisor, it should restart automatically.\n\n"
                    f"Confirm with: {p}restart confirm"
                ),
                mtype="groupchat",
            )
            return

        await self.bot_send_message(
            mto=room,
            mbody="♻️ Restart confirmed. Shutting down now; supervisor should restart the bot.",
            mtype="groupchat",
            encrypted=False,
        )

        asyncio_module = commands_module_attr("asyncio", asyncio)
        restart_task = asyncio_module.create_task(self._restart_process())
        self._restart_task = restart_task

        if isinstance(restart_task, asyncio.Task):
            restart_task.add_done_callback(self._clear_restart_task)

    def _clear_restart_task(self, task: asyncio.Task) -> None:
        """Drop the stored restart task reference once it has completed."""
        if getattr(self, "_restart_task", None) is task:
            self._restart_task = None

    async def _restart_process(self) -> None:
        """Flush state, disconnect, and terminate the process for supervisor restart."""
        asyncio_module = commands_module_attr("asyncio", asyncio)
        os_module = commands_module_attr("os", os)

        await asyncio_module.sleep(0.5)

        shutdown = getattr(self, "shutdown", None)
        if callable(shutdown):
            try:
                result = shutdown()
                if inspect.isawaitable(result):
                    await result
            except Exception as exc:
                log.warning("Restart: graceful shutdown failed: %s", exc)
        else:
            try:
                if hasattr(self, "flush_redaction_index"):
                    await self.flush_redaction_index()
            except Exception as exc:
                log.warning("Restart: failed to flush redaction index: %s", exc)
            try:
                if hasattr(self, "stop_background_tasks"):
                    await self.stop_background_tasks()
            except Exception as exc:
                log.warning("Restart: failed to stop background tasks cleanly: %s", exc)
            try:
                disconnect = getattr(self, "disconnect", None)
                if callable(disconnect):
                    try:
                        result = disconnect(wait=False)
                    except TypeError:
                        result = disconnect()
                    if inspect.isawaitable(result):
                        await result
            except Exception as exc:
                log.warning("Restart: failed to disconnect cleanly: %s", exc)

        log.info("Restart: exiting process now")
        os_module._exit(SUPERVISOR_RESTART_EXIT_CODE)
