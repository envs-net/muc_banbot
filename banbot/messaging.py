"""Centralized BanBot message output helpers.

This module intentionally keeps Slixmpp's low-level ``send_message()``
untouched and provides a BanBot-owned output layer instead.  Future
transport-specific behavior, such as OMEMO encryption, can be added here
without touching every command/mixin again.
"""

import inspect
import logging
from typing import TYPE_CHECKING, Any

from envs_xmpp_core.xmpp.messaging import ReplyRoute, TaskLocalReplyRoute
from envs_xmpp_core.xmpp.omemo import TaskLocalEncryptionMode
from envs_xmpp_core.xmpp.outbound import (
    ensure_message_origin_id,
    plan_outbound_message,
    transport_accepted,
)

log = logging.getLogger(__name__)

_REPLY_ENCRYPTION = TaskLocalEncryptionMode("banbot_reply_encrypted")
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
    ):
        """Set the encryption preference for replies created in the current task."""
        return _REPLY_ENCRYPTION.set(encrypted)

    def _reset_reply_encryption_context(
        self,
        token,
    ) -> None:
        """Restore the previous reply encryption context."""
        _REPLY_ENCRYPTION.reset(token)

    def _get_reply_encryption_context(self) -> bool | None:
        """Return the current task's reply encryption preference, if any."""
        return _REPLY_ENCRYPTION.get()

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
        origin_id: str | None = None,
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

        if encrypted is True and not should_encrypt:
            # An explicit (or inherited) OMEMO reply must never become
            # plaintext merely because the encryption backend is disabled.
            # Preserve the application's explicitly opted-in fallback policy.
            if not getattr(self, "omemo_plaintext_fallback", False):
                if raise_on_failure:
                    raise RuntimeError("Explicit OMEMO encryption requested but unavailable")
                log.warning("Rejecting encrypted send to %s: OMEMO unavailable", mto)
                return None

        if should_encrypt:
            try:
                result = await self._send_omemo_message(
                    mto=mto,
                    mbody=mbody,
                    mtype=mtype,
                    **({"origin_id": origin_id} if origin_id is not None else {}),
                    **kwargs,
                )
                if raise_on_failure and not transport_accepted(result):
                    raise RuntimeError("Encrypted transport rejected outbound stanza")
                return result
            except Exception as exc:
                log.warning("Encrypted send to %s failed: %s", mto, exc)
                if not getattr(self, "omemo_plaintext_fallback", False):
                    if raise_on_failure:
                        raise
                    return None
                log.warning("Falling back to plaintext send for %s", mto)

        if origin_id is not None:
            # A durable retry must reuse its recorded stanza identity. Calling
            # send_message() here would generate a fresh id for every replay.
            make_message = getattr(self, "make_message", None)
            if not callable(make_message):
                raise RuntimeError("Durable transport cannot create a stanza with a stable id")
            stanza = make_message(mto=mto, mbody=mbody, mtype=mtype, **kwargs)
            ensure_message_origin_id(stanza, origin_id, require_stanza_id=True)
            result = stanza.send()
            result = await result if inspect.isawaitable(result) else result
        else:
            result = self.send_message(
                mto=mto,
                mbody=mbody,
                mtype=mtype,
                **kwargs,
            )
            if inspect.isawaitable(result):
                result = await result
        if raise_on_failure and not transport_accepted(result):
            raise RuntimeError("Transport rejected outbound stanza")
        return result

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
        route = ReplyRoute(*reply_target) if reply_target is not None else None
        plan = plan_outbound_message(
            target=mto,
            message_type=mtype,
            reply_route=route,
            encrypted=encrypted,
            inherited_encryption=self._get_reply_encryption_context(),
            durable=durable,
        )
        mto, mtype, encrypted = plan.route.target, plan.route.message_type, plan.encrypted

        if durable:
            if kwargs:
                raise ValueError("durable messages cannot persist transport-specific keyword arguments")
            if not plan.can_persist_without_encryption_context:
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
