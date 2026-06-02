"""Public policy/rules command handling."""

from .context import admin_room


class CommandPolicyMixin:
    def _format_public_policy_text(self, text: str, room: str) -> str:
        """Format public policy text with simple placeholders."""
        replacements = {
            "bot_name": "muc_banbot",
            "prefix": self.command_prefix,
            "room": room,
            "room_count": str(len(getattr(self, "protected_rooms", []))),
            "admin_room": admin_room(),
        }

        formatted = text

        for key, value in replacements.items():
            formatted = formatted.replace("{" + key + "}", value)

        # Allow admins to enter multiline text via literal \n in chat.
        formatted = formatted.replace("\\n", "\n")

        return formatted.strip()

    async def _cmd_public_policy_show(self, room: str) -> None:
        """Show public policy text in a protected room."""
        enabled, text = await self.get_public_policy()

        # In protected rooms this should be quiet when disabled/unset.
        # Unknown commands are already silent there, so keep this optional too.
        if not enabled or not text.strip():
            return

        await self.bot_send_message(
            mto=room,
            mbody=self._format_public_policy_text(text, room),
            mtype="groupchat",
        )

    async def cmd_policy(self, args: list[str], room: str) -> None:
        """Admin command to manage the public policy/rules text."""
        p = self.command_prefix

        if args and args[0].lower() in {"help", "usage"}:
            await self.bot_send_message(
                mto=room,
                mbody=self._policy_usage_text(),
                mtype="groupchat",
            )
            return

        if not args or args[0].lower() in {"show", "list"}:
            enabled, text = await self.get_public_policy()

            if not text.strip():
                await self.bot_send_message(
                    mto=room,
                    mbody=(
                        "ℹ️ No public policy text is configured.\n\n"
                        f"{self._policy_usage_text()}"
                    ),
                    mtype="groupchat",
                )
                return

            status = "enabled" if enabled else "disabled"
            preview = self._format_public_policy_text(text, room)

            await self.bot_send_message(
                mto=room,
                mbody=(
                    f"📜 Public policy is currently {status}.\n\n"
                    f"{preview}\n\n"
                    f"{self._policy_usage_text().replace('Usage:', 'Commands:', 1)}"
                ),
                mtype="groupchat",
            )
            return

        action = args[0].lower()

        if action == "set":
            if len(args) < 2:
                await self.bot_send_message(
                    mto=room,
                    mbody=(
                        f"❌ Usage: {p}policy set <text>\n"
                        "Use literal \\n for line breaks.\n"
                        "Placeholders: {prefix}, {room}, {room_count}, {admin_room}, {bot_name}"
                    ),
                    mtype="groupchat",
                )
                return

            text = " ".join(args[1:]).strip()
            await self.set_public_policy_text(text, enabled=True)

            await self.bot_send_message(
                mto=room,
                mbody=(
                    "✅ Public policy text saved and enabled.\n\n"
                    f"{self._format_public_policy_text(text, room)}"
                ),
                mtype="groupchat",
            )
            return

        if action == "enable":
            _enabled, text = await self.get_public_policy()

            if not text.strip():
                await self.bot_send_message(
                    mto=room,
                    mbody=f"⚠️ No public policy text is configured. Use {p}policy set <text> first.",
                    mtype="groupchat",
                )
                return

            if enabled:
                await self.bot_send_message(
                    mto=room,
                    mbody="ℹ️ Public policy command is already enabled.",
                    mtype="groupchat",
                )
                return

            await self.set_public_policy_enabled(True)
            await self.bot_send_message(
                mto=room,
                mbody="✅ Public policy command enabled.",
                mtype="groupchat",
            )
            return

        if action == "disable":
            enabled, _text = await self.get_public_policy()

            if not enabled:
                await self.bot_send_message(
                    mto=room,
                    mbody="ℹ️ Public policy command is already disabled.",
                    mtype="groupchat",
                )
                return

            await self.set_public_policy_enabled(False)
            await self.bot_send_message(
                mto=room,
                mbody="✅ Public policy command disabled.",
                mtype="groupchat",
            )
            return

        if action in ("clear", "delete", "remove"):
            _enabled, text = await self.get_public_policy()

            if not text.strip():
                await self.bot_send_message(
                    mto=room,
                    mbody="ℹ️ No public policy text is configured.",
                    mtype="groupchat",
                )
                return

            await self.clear_public_policy()
            await self.bot_send_message(
                mto=room,
                mbody="✅ Public policy text cleared and disabled.",
                mtype="groupchat",
            )
            return

        await self.bot_send_message(
            mto=room,
            mbody=(
                f"❌ Unknown policy action: {action}\n"
                f"Available: show / set / enable / disable / clear / delete / remove / help / usage"
            ),
            mtype="groupchat",
        )

    async def _dispatch_policy_command(self, room: str, nick: str, args: list[str], cmd: str) -> None:
        await self.cmd_policy(args, room)
