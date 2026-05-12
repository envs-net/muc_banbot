"""Centralized BanBot message output helpers.

This module intentionally keeps Slixmpp's low-level ``send_message()``
untouched and provides a BanBot-owned output layer instead.  Future
transport-specific behavior, such as OMEMO encryption, can be added here
without touching every command/mixin again.
"""

import logging
from contextvars import ContextVar, Token
from typing import Any

log = logging.getLogger(__name__)

_REPLY_ENCRYPTED: ContextVar[bool | None] = ContextVar("banbot_reply_encrypted", default=None)


class MessagingMixin:
    def _set_reply_encryption_context(self, encrypted: bool | None) -> Token[bool | None]:
        """Set the encryption preference for replies created in the current task."""
        return _REPLY_ENCRYPTED.set(encrypted)

    def _reset_reply_encryption_context(self, token: Token[bool | None]) -> None:
        """Restore the previous reply encryption context."""
        _REPLY_ENCRYPTED.reset(token)

    def _get_reply_encryption_context(self) -> bool | None:
        """Return the current task's reply encryption preference, if any."""
        return _REPLY_ENCRYPTED.get()

    async def bot_send_message(
        self,
        *,
        mto: str,
        mbody: str,
        mtype: str = "groupchat",
        encrypted: bool | None = None,
        **kwargs: Any,
    ) -> Any:
        """
        Send a bot-generated message through the central output layer.

        When ``encrypted`` is not specified, replies inherit the encryption mode
        of the incoming command message via a task-local context.  This lets the
        bot answer OMEMO commands with OMEMO and plaintext commands with
        plaintext without every command handler having to know about OMEMO.
        """
        if encrypted is None:
            encrypted = self._get_reply_encryption_context()

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
                    return None
                log.warning("Falling back to plaintext send for %s", mto)

        return self.send_message(
            mto=mto,
            mbody=mbody,
            mtype=mtype,
            **kwargs,
        )
