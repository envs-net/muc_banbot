"""Import command handling for managed ban CSV files."""

import inspect
import logging

log = logging.getLogger(__name__)


class CommandImportMixin:
    async def _handle_import_command(self, args: list[str], room: str, nick: str) -> None:
        """Import bans from a managed CSV file and announce a compact summary."""
        if len(args) < 1:
            await self.bot_send_message(
                mto=room,
                mbody=f"❌ Usage: {self.command_prefix}import <filename> [dryrun]",
                mtype="groupchat",
            )
            return

        filename = args[0]
        dry_run = len(args) >= 2 and args[1].lower() in {"dryrun", "dry-run", "check"}
        actor_jid = self._actor_jid_from_room_nick(room, nick)
        previous_backup = getattr(self, "last_database_backup_file", None)
        import_kwargs = {"actor": actor_jid}
        # Lightweight tests and older mixins may not support dry_run yet.
        import_sig = inspect.signature(self.import_bans_from_csv)
        supports_dry_run = (
            "dry_run" in import_sig.parameters
            or any(
                parameter.kind == inspect.Parameter.VAR_KEYWORD
                for parameter in import_sig.parameters.values()
            )
        )
        if supports_dry_run:
            import_kwargs["dry_run"] = dry_run

        successful, skipped, errors = await self.import_bans_from_csv(
            filename,
            **import_kwargs,
        )
        import_backup = getattr(self, "last_database_backup_file", None)
        if import_backup == previous_backup:
            import_backup = None

        heading = "📥 Import Dry-Run Results" if dry_run else "📥 Import Results"
        result_msg = (
            f"{heading}:\n"
            f"✅ Successful: {successful}\n"
            f"⚠️ Skipped: {skipped}"
        )
        if dry_run:
            result_msg += "\nNo backup created and no database changes made."

        if import_backup:
            result_msg += f"\n💾 Full backup before import: {import_backup}"

        if errors:
            result_msg += f"\n\n❌ Errors ({len(errors)}):\n"
            result_msg += "\n".join(errors[:10])
            if len(errors) > 10:
                result_msg += f"\n... and {len(errors) - 10} more errors"

        await self.bot_send_message(mto=room, mbody=result_msg, mtype="groupchat")
        log.info(
            "Import completed: %d successful, %d skipped, %d errors",
            successful,
            skipped,
            len(errors),
        )
        self.log_event(
            logging.INFO,
            "import_completed",
            actor=actor_jid,
            filename=filename,
            dry_run=dry_run,
            successful=successful,
            skipped=skipped,
            errors=len(errors),
            backup=import_backup,
        )
        await self.audit_event(
            "import_completed",
            actor=actor_jid,
            target_type="import",
            target=filename,
            details={
                "filename": filename,
                "dry_run": dry_run,
                "successful": successful,
                "skipped": skipped,
                "errors": len(errors),
                "backup": import_backup,
            },
        )
