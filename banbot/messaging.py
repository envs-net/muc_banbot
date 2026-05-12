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

        For now this async wrapper performs a synchronous Slixmpp
        ``send_message()`` call internally. Keeping the public output layer
        async makes it ready for OMEMO backends, which need awaitable
        encryption and recipient/session handling before sending.
        The ``encrypted`` flag is accepted already so callers do not need to
        change again when encrypted transports such as OMEMO are implemented.
        """
        if encrypted is not None:
            log.debug(
                "Encrypted send requested for %s, but no encryption backend is implemented yet",
                mto,
            )

        return self.send_message(
            mto=mto,
            mbody=mbody,
            mtype=mtype,
            **kwargs,
        )
