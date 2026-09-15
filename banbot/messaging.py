"""Centralized BanBot message output helpers.

This module intentionally keeps Slixmpp's low-level ``send_message()``
untouched and provides a BanBot-owned output layer instead.  Future
transport-specific behavior, such as OMEMO encryption, can be added here
without touching every command/mixin again.
"""

import asyncio
import logging
from contextvars import ContextVar, Token
from typing import TYPE_CHECKING, Any

from envs_xmpp_core.xmpp.messaging import ReplyRoute, TaskLocalReplyRoute

log = logging.getLogger(__name__)

_EncryptionContext = tuple[object | None, bool | None]
_REPLY_ENCRYPTED: ContextVar[_EncryptionContext | None] = ContextVar(
    "banbot_reply_encrypted",
    default=None,
)
_REPLY_ROUTES = TaskLocalReplyRoute("banbot_reply_target")

if TYPE_CHECKING:
    from .contracts import MessagingMixinHost

    class _MessagingMixinContract(MessagingMixinHost):
        pass
else:
    class _MessagingMixinContract:
        pass


class MessagingMixin(_MessagingMixinContract):
    def _set_reply_encryption_context(
        self,
        encrypted: bool | None,
    ) -> Token[_EncryptionContext | None]:
        """Set the encryption preference for replies created in the current task."""
        return _REPLY_ENCRYPTED.set((asyncio.current_task(), encrypted))

    def _reset_reply_encryption_context(
        self,
        token: Token[_EncryptionContext | None],
    ) -> None:
        """Restore the previous reply encryption context."""
        _REPLY_ENCRYPTED.reset(token)

    def _get_reply_encryption_context(self) -> bool | None:
        """Return the current task's reply encryption preference, if any."""
        value = _REPLY_ENCRYPTED.get()
        if value is None:
            return None
        owner_task, encrypted = value
        if owner_task is not None and asyncio.current_task() is not owner_task:
            return None
        return encrypted

    def _set_reply_target_context(
        self,
        mto: str,
        mtype: str,
    ):
        """Route command output in only the current asyncio task to one target."""
        return _REPLY_ROUTES.set(mto, mtype)

    def _reset_reply_target_context(self, token) -> None:
        """Restore the previous task-local output target."""
        _REPLY_ROUTES.reset(token)

    def _get_reply_target_context(self) -> tuple[str, str] | None:
        """Return the shared task-local output target."""
        route: ReplyRoute | None = _REPLY_ROUTES.get()
        if route is None:
            return None
        return route.target, route.message_type

    async def _send_message_transport(
        self,
        *,
        mto: str,
        mbody: str,
        mtype: str,
        encrypted: bool | None,
        raise_on_failure: bool = False,
        **kwargs: Any,
    ) -> Any:
        """Send one already-routed message without durable requeueing."""
        should_encrypt = False
        if hasattr(self, "_should_encrypt_message"):
            should_encrypt = self._should_encrypt_message(
                mto=mto,
                mtype=mtype,
                encrypted=encrypted,
            )

        if should_encrypt:
            try:
                return await self._send_omemo_message(
                    mto=mto,
                    mbody=mbody,
                    mtype=mtype,
                    **kwargs,
                )
            except Exception as exc:
                log.warning("Encrypted send to %s failed: %s", mto, exc)
                if not getattr(self, "omemo_plaintext_fallback", False):
                    if raise_on_failure:
                        raise
                    return None
                log.warning("Falling back to plaintext send for %s", mto)

        return self.send_message(
            mto=mto,
            mbody=mbody,
            mtype=mtype,
            **kwargs,
        )

    async def bot_send_message(
        self,
        *,
        mto: str,
        mbody: str,
        mtype: str = "groupchat",
        encrypted: bool | None = None,
        durable: bool = False,
        category: str = "message",
        dedupe_key: str | None = None,
        max_attempts: int | None = None,
        **kwargs: Any,
    ) -> Any:
        """Send through the central routing/encryption/durability layer.

        Durable sends are queue-first and therefore at-least-once.  They are
        intended for proactive operational messages; task-local encryption is
        re-evaluated at delivery time so queue persistence never downgrades an
        ADMIN_ROOM message from the configured OMEMO policy.
        """
        reply_target = self._get_reply_target_context()
        if reply_target is not None:
            mto, mtype = reply_target

        if encrypted is None:
            encrypted = self._get_reply_encryption_context()

        if durable:
            if kwargs:
                raise ValueError("durable messages cannot persist transport-specific keyword arguments")
            if encrypted is True:
                raise ValueError("durable messages cannot persist task-local explicit encryption state")
            enqueue = getattr(self, "enqueue_durable_message", None)
            if not callable(enqueue):
                return await self._send_message_transport(
                    mto=mto,
                    mbody=mbody,
                    mtype=mtype,
                    encrypted=encrypted,
                )
            message_id = await enqueue(
                destination=mto,
                body=mbody,
                message_type=mtype,
                category=category,
                dedupe_key=dedupe_key,
                max_attempts=max_attempts,
            )
            return message_id is not None

        return await self._send_message_transport(
            mto=mto,
            mbody=mbody,
            mtype=mtype,
            encrypted=encrypted,
            **kwargs,
        )
