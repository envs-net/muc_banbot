"""Centralized BanBot message output helpers.

This module intentionally keeps Slixmpp's low-level ``send_message()``
untouched and provides a BanBot-owned output layer instead.  Future
transport-specific behavior, such as OMEMO encryption, can be added here
without touching every command/mixin again.
"""

import logging
from typing import Any

log = logging.getLogger(__name__)


class MessagingMixin:
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

        When OMEMO is enabled and selected for the target, this wrapper routes
        the message through the encrypted send path. Otherwise it performs a
        normal Slixmpp ``send_message()`` call internally.
        """
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
