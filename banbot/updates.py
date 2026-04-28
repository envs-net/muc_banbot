"""GitHub release/version check helpers."""

import asyncio
import logging
import re
import urllib.request

from config import ADMIN_ROOM

from ._version import __version__

log = logging.getLogger(__name__)


class UpdateMixin:
    def _parse_version_tuple(self, version: str) -> tuple[int, ...]:
        parts = re.findall(r"\d+", version)
        return tuple(int(p) for p in parts)


    def _is_remote_version_newer(self, remote_version: str, local_version: str) -> bool:
        return self._parse_version_tuple(remote_version) > self._parse_version_tuple(local_version)


    def _fetch_latest_release_version_sync(self) -> str:
        """
        Fetch the latest GitHub release version by following the /releases/latest redirect.
        Example final URL:
            https://github.com/envs-net/muc_banbot/releases/tag/v1.3.0
        Returns:
            1.3.0
        """
        if not self.version_check_url:
            raise ValueError("VERSION_CHECK_URL is not configured")

        req = urllib.request.Request(
            self.version_check_url,
            headers={"User-Agent": f"muc_banbot/{__version__}"}
        )

        with urllib.request.urlopen(req, timeout=15) as response:
            final_url = response.geturl()

        marker = "/releases/tag/"
        if marker not in final_url:
            raise ValueError(f"Unexpected release redirect URL: {final_url}")

        tag = final_url.split(marker, 1)[1].strip().strip("/")
        if not tag:
            raise ValueError("Could not extract release tag from redirect URL")

        return tag.lstrip("v")


    async def check_for_updates_once(
        self,
        announce: bool = True
    ) -> tuple[bool, str | None, str | None]:
        """
        Check once whether a newer bot version is available.
        Returns: (is_update_available, remote_version, error_message)
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
                    self.version_check_url
                )

                if announce and self.last_update_notified_version != remote_version:
                    self.send_message(
                        mto=ADMIN_ROOM,
                        mbody=(
                            f"⬆️ New bot version available: {remote_version}\n"
                            f"Current version: {current_version}\n"
                            f"Release page: {self.version_check_url}"
                        ),
                        mtype="groupchat"
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
