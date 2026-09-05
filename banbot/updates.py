"""GitHub release/version check helpers."""

import asyncio
import logging
import urllib.request

from envs_xmpp_core.release.github import (
    fetch_latest_release_version_via_github_api_sync as core_fetch_api,
)
from envs_xmpp_core.release.github import (
    fetch_latest_release_version_via_redirect_sync as core_fetch_redirect,
)
from envs_xmpp_core.release.github import (
    github_api_url_from_release_url,
)
from envs_xmpp_core.release.versions import compare_versions, parse_version_tuple

from config import ADMIN_ROOM

from ._version import __version__

log = logging.getLogger(__name__)

_LAST_STARTED_VERSION_KEY = "last_successful_start_version"


class UpdateMixin:
    def _parse_version_tuple(self, version: str) -> tuple[int, ...]:
        return parse_version_tuple(version)

    def _is_remote_version_newer(self, remote_version: str, local_version: str) -> bool:
        return compare_versions(remote_version, local_version) > 0

    def _github_api_url_from_release_url(self, release_url: str) -> str | None:
        return github_api_url_from_release_url(release_url)


    async def prepare_startup_version_notice(self, *, reconnecting: bool) -> str | None:
        """Load the last successfully started version before startup completes."""
        self.previous_startup_version = None
        if reconnecting or not getattr(self, "db", None):
            return None

        try:
            async with self.db.execute(
                "SELECT value FROM bot_metadata WHERE key = ?",
                (_LAST_STARTED_VERSION_KEY,),
            ) as cursor:
                row = await cursor.fetchone()
        except Exception as exc:
            log.warning("Could not read previous startup version: %s", exc)
            return None

        if row and row[0]:
            self.previous_startup_version = str(row[0]).lstrip("v").strip()
        return self.previous_startup_version

    async def finalize_startup_version_notice(self, *, reconnecting: bool) -> bool:
        """Announce a completed upgrade and persist the successfully started version."""
        if reconnecting or not getattr(self, "db", None):
            return False

        current_version = __version__.lstrip("v").strip()
        previous_version = getattr(self, "previous_startup_version", None)
        was_updated = bool(
            previous_version
            and self._is_remote_version_newer(current_version, previous_version)
        )

        if was_updated and bool(getattr(self, "announce_startup", True)):
            try:
                await self.bot_send_message(
                    mto=ADMIN_ROOM,
                    mbody=(
                        f"⬆️ BanBot updated successfully: {previous_version} → {current_version}\n"
                        "The restart completed and all configured rooms and bans were synchronized."
                    ),
                    mtype="groupchat",
                )
            except Exception as exc:
                # Do not fail an otherwise healthy startup because the optional
                # notification could not be delivered. Keep the old DB value so
                # the message can be retried after the next restart.
                log.warning("Could not announce completed bot update: %s", exc)
                return False

        try:
            await self.db.execute(
                """
                INSERT INTO bot_metadata (key, value, updated_at)
                VALUES (?, ?, strftime('%s','now'))
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value,
                    updated_at = excluded.updated_at
                """,
                (_LAST_STARTED_VERSION_KEY, current_version),
            )
            await self.db.commit()
        except Exception as exc:
            log.warning("Could not persist successful startup version: %s", exc)
            return False

        return was_updated

    def _fetch_latest_release_version_via_github_api_sync(self) -> str:
        try:
            return core_fetch_api(
                self.version_check_url,
                user_agent=f"muc_banbot/{__version__}",
                timeout=15,
                urlopen=urllib.request.urlopen,
            )
        except ValueError as exc:
            if "supported GitHub" in str(exc):
                raise ValueError("VERSION_CHECK_URL is not a supported GitHub releases URL") from exc
            raise

    def _fetch_latest_release_version_via_redirect_sync(self) -> str:
        if not self.version_check_url:
            raise ValueError("VERSION_CHECK_URL is not configured")
        return core_fetch_redirect(
            self.version_check_url,
            user_agent=f"muc_banbot/{__version__}",
            timeout=15,
            urlopen=urllib.request.urlopen,
        )

    def _fetch_latest_release_version_sync(self) -> str:
        """
        Fetch the latest release version.

        Prefer the GitHub API when VERSION_CHECK_URL points to GitHub. Fall back
        to the old redirect parser so non-API-compatible setups still work.
        """
        if not self.version_check_url:
            raise ValueError("VERSION_CHECK_URL is not configured")

        try:
            return self._fetch_latest_release_version_via_github_api_sync()
        except Exception as api_error:
            log.debug(
                "Version check via GitHub API failed, falling back to redirect: %s",
                api_error,
            )

        return self._fetch_latest_release_version_via_redirect_sync()

    async def check_for_updates_once(
        self,
        announce: bool = True,
    ) -> tuple[bool, str | None, str | None]:
        """
        Check once whether a newer bot version is available.

        Returns:
            (is_update_available, remote_version, error_message)
        """
        if not self.version_check_enabled or not self.version_check_url:
            return False, None, "Version check is disabled or URL is missing"

        try:
            remote_version = await asyncio.to_thread(self._fetch_latest_release_version_sync)
            self.last_version_check_result = remote_version

            current_version = __version__.lstrip("v").strip()

            if self._is_remote_version_newer(remote_version, current_version):
                log.info(
                    "⬆️ New bot version available: remote=%s local=%s url=%s",
                    remote_version,
                    current_version,
                    self.version_check_url,
                )

                if announce and self.last_update_notified_version != remote_version:
                    await self.bot_send_message(
                        mto=ADMIN_ROOM,
                        mbody=(
                            f"⬆️ New bot version available: {remote_version}\n"
                            f"Current version: {current_version}\n"
                            f"Release page: {self.version_check_url}"
                        ),
                        mtype="groupchat",
                    )
                    self.last_update_notified_version = remote_version

                return True, remote_version, None

            return False, remote_version, None

        except Exception as e:
            log.warning("Version check failed: %s", e)
            return False, None, str(e)

    async def version_check_worker(self) -> None:
        """
        Periodically check whether a newer bot version is available.
        """
        while True:
            try:
                await self.check_for_updates_once(announce=True)
            except asyncio.CancelledError:
                log.info("version_check_worker cancelled")
                raise
            except Exception as e:
                log.warning("Error in version_check_worker: %s", e)

            await asyncio.sleep(self.version_check_interval)
