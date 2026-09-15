"""GitHub release/version check helpers."""

import asyncio
import logging
import urllib.request
from typing import TYPE_CHECKING

from envs_xmpp_core.release.checks import evaluate_release_check
from envs_xmpp_core.release.github import (
    fetch_latest_release_version_via_github_api_sync as core_fetch_api,
)
from envs_xmpp_core.release.github import (
    fetch_latest_release_version_via_redirect_sync as core_fetch_redirect,
)
from envs_xmpp_core.release.github import github_api_url_from_release_url
from envs_xmpp_core.release.state import ReleaseState
from envs_xmpp_core.release.transitions import (
    merge_pending_version_transition,
    version_transition,
)
from envs_xmpp_core.release.versions import compare_versions, parse_version_tuple

from config import ADMIN_ROOM

from ._version import __version__
from .release_state import (
    load_release_state_with_legacy_migration,
    release_state_repository,
)
from .task_supervisor import sleep_with_heartbeat

log = logging.getLogger(__name__)

if TYPE_CHECKING:
    from .contracts import UpdateMixinHost

    class _UpdateMixinContract(UpdateMixinHost):
        pass
else:
    class _UpdateMixinContract:
        pass


class UpdateMixin(_UpdateMixinContract):
    version_check_enabled: bool
    version_check_interval: float
    version_check_url: str | None
    last_version_check_result: str | None
    last_update_notified_version: str | None
    previous_startup_version: str | None
    _startup_release_state: ReleaseState | None

    def _parse_version_tuple(self, version: str) -> tuple[int, ...]:
        return parse_version_tuple(version)

    def _is_remote_version_newer(self, remote_version: str, local_version: str) -> bool:
        return compare_versions(remote_version, local_version) > 0

    def _github_api_url_from_release_url(self, release_url: str) -> str | None:
        return github_api_url_from_release_url(release_url)


    async def prepare_startup_version_notice(self, *, reconnecting: bool) -> str | None:
        """Load the shared release state before startup completes."""
        self.previous_startup_version = None
        # ``None`` means that no trustworthy snapshot was loaded.  Keep that
        # distinct from a successfully loaded empty ReleaseState (first start),
        # otherwise a transient read failure could later overwrite persisted
        # pending-release state with a fabricated empty baseline.
        self._startup_release_state = None
        if reconnecting or not getattr(self, "db", None):
            return None

        repository = release_state_repository(self)
        try:
            await repository.setup()
            state = await load_release_state_with_legacy_migration(self, repository)
        except Exception as exc:
            log.warning("Could not read previous startup version: %s", exc)
            return None

        self._startup_release_state = state
        self.previous_startup_version = state.version
        return state.version

    async def finalize_startup_version_notice(self, *, reconnecting: bool) -> bool:
        """Persist one successful startup and deliver any pending upgrade notice."""
        if reconnecting or not getattr(self, "db", None):
            return False

        repository = release_state_repository(self)
        current_version = __version__.lstrip("v").strip()
        state: ReleaseState | None = getattr(self, "_startup_release_state", None)
        if state is None:
            try:
                await repository.setup()
                state = await load_release_state_with_legacy_migration(self, repository)
            except Exception as exc:
                # Do not manufacture an empty baseline here.  Persisting one
                # after a failed read could erase an older version or pending
                # update announcement that is still safely stored in SQLite.
                log.warning("Could not load startup release state: %s", exc)
                return False

        previous_version = state.version
        current_transition = version_transition(previous_version, current_version)
        pending = merge_pending_version_transition(
            previous_version,
            current_version,
            state.pending_announcement,
        )
        if isinstance(pending, dict):
            pending_transition = version_transition(pending["from"], pending["to"])
            if not pending_transition.is_upgrade:
                pending = None

        announce = bool(getattr(self, "announce_startup", True))
        persisted_pending = pending if announce else None
        next_state = ReleaseState(
            version=current_version,
            pending_from=(
                persisted_pending.get("from")
                if isinstance(persisted_pending, dict)
                else None
            ),
            pending_to=(
                persisted_pending.get("to")
                if isinstance(persisted_pending, dict)
                else None
            ),
        )
        try:
            await repository.save(next_state)
        except Exception as exc:
            log.warning("Could not persist successful startup version: %s", exc)
            return False

        self._startup_release_state = next_state
        if not isinstance(persisted_pending, dict):
            return current_transition.is_upgrade

        notice_from = str(persisted_pending["from"])
        notice_to = str(persisted_pending["to"])
        try:
            await self.bot_send_message(
                mto=ADMIN_ROOM,
                mbody=(
                    f"⬆️ BanBot updated successfully: {notice_from} → {notice_to}\n"
                    "The restart completed and all configured rooms and bans were synchronized."
                ),
                mtype="groupchat",
            )
        except Exception as exc:
            # The shared state already records the successful version and keeps
            # the pending transition so the message can be retried next start.
            log.warning("Could not announce completed bot update: %s", exc)
            return False

        try:
            await repository.clear_pending_if_matches(notice_from, notice_to)
        except Exception as exc:
            log.warning("Could not clear delivered startup update state: %s", exc)
            return False
        self._startup_release_state = ReleaseState(version=current_version)
        return True

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

        current_version = __version__.lstrip("v").strip()
        decision = await evaluate_release_check(
            current_version,
            self._fetch_latest_release_version_sync,
            announce=announce,
            last_notified_version=self.last_update_notified_version,
        )
        result = decision.result
        if result.error:
            log.warning("Version check failed: %s", result.error)
            return result.as_tuple()

        remote_version = result.remote_version
        self.last_version_check_result = remote_version

        if result.update_available and remote_version is not None:
            log.info(
                "⬆️ New bot version available: remote=%s local=%s url=%s",
                remote_version,
                current_version,
                self.version_check_url,
            )

            if decision.notification_version is not None:
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

        return result.as_tuple()

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

            await sleep_with_heartbeat(
                self,
                "version-check-worker",
                self.version_check_interval,
                sleep_func=asyncio.sleep,
            )
