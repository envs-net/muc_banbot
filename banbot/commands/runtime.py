"""Runtime and bot-control admin command dispatch helpers."""

import asyncio
import inspect
import logging
import os
from .._version import __version__
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
            await self._cmd_status(room)
            return True

        if cmd == "tasks":
            await self._cmd_tasks(room, args)
            return True

        if cmd in ("checkupdate", "updatecheck"):
            await self._cmd_checkupdate(room)
            return True

        return False

    async def _cmd_tasks(self, room: str, args: list[str], *, mtype: str = "groupchat") -> None:
        """Show supervised background workers and runtime watchdog health."""
        mode = args[0].lower().strip() if args else "active"
        if len(args) > 1 or mode not in {"active", "all", "failed"}:
            await self.bot_send_message(
                mto=room,
                mbody=f"❌ {self._tasks_usage_text()}",
                mtype=mtype,
            )
            return

        supervisor = getattr(self, "tasks", None)
        snapshot = getattr(supervisor, "snapshot", None)
        if not callable(snapshot):
            await self.bot_send_message(
                mto=room,
                mbody="⚠️ Background task supervisor is not available.",
                mtype=mtype,
            )
            return

        include_done = mode in {"all", "failed"}
        task_infos = list(snapshot(include_done=include_done))
        if mode == "failed":
            task_infos = [info for info in task_infos if info.status == "failed"]

        all_infos = list(snapshot(include_done=True))
        running_count = sum(info.status == "running" for info in all_infos)
        failed_count = sum(info.status == "failed" for info in all_infos)
        other_count = len(all_infos) - running_count - failed_count

        lines = [
            "🧵 Background Tasks: "
            f"{running_count} running, {failed_count} failed, {other_count} other"
        ]

        if task_infos:
            for info in task_infos:
                icon = {
                    "running": "✅",
                    "failed": "❌",
                    "done": "☑️",
                    "cancelled": "⏹️",
                }.get(info.status, "ℹ️")
                detail = f"{icon} {info.name} — {info.status} • restarts: {info.restart_count}"
                if info.last_error:
                    detail += f" • last error: {info.last_error}"
                lines.append(detail)
        elif mode == "failed":
            lines.append("✅ No failed background tasks.")
        else:
            lines.append("ℹ️ No background tasks match this view.")

        watchdog = getattr(self, "runtime_watchdog", None)
        runtime_state = getattr(watchdog, "runtime_state", None)
        if callable(runtime_state):
            state = runtime_state()
            enabled = bool(state.get("enabled"))
            worker_running = bool(state.get("worker_running"))
            suppressed = int(state.get("heartbeat_suppressed", 0) or 0)
            last_error = state.get("last_error")

            if not enabled:
                watchdog_status = "disabled"
                watchdog_icon = "⏹️"
            elif not worker_running or last_error:
                watchdog_status = "unhealthy"
                watchdog_icon = "❌"
            elif suppressed:
                watchdog_status = "degraded"
                watchdog_icon = "⚠️"
            else:
                watchdog_status = "healthy"
                watchdog_icon = "✅"

            lines.extend(
                [
                    "",
                    "🐕 Runtime Watchdog",
                    f"{watchdog_icon} Status: {watchdog_status}",
                    "systemd watchdog: "
                    + ("active" if state.get("systemd_active") else "inactive"),
                    "event-loop lag: "
                    f"{float(state.get('last_lag_seconds', 0.0) or 0.0):.3f}s current / "
                    f"{float(state.get('max_lag_seconds', 0.0) or 0.0):.3f}s max",
                    "heartbeats: "
                    f"{int(state.get('heartbeats', 0) or 0)} • "
                    f"suppressed: {suppressed}",
                ]
            )
            if last_error:
                lines.append(f"last error: {last_error}")

        await self.bot_send_message(
            mto=room,
            mbody="\n".join(lines),
            mtype=mtype,
        )

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
        # Give the confirmation message a short chance to leave the XMPP stream.
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
            # Compatibility for lightweight embedders that use the command mixin
            # without the full BanBot lifecycle implementation.
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
