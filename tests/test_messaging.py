import asyncio

import pytest

from banbot.messaging import MessagingMixin


class MessagingBot(MessagingMixin):
    def __init__(self):
        self.sent = []
        self.encrypted_sent = []
        self.omemo_plaintext_fallback = False
        self.encrypt_decision = False
        self.encrypt_raises = None

    def _should_encrypt_message(self, *, mto, mtype, encrypted):
        self.last_decision_args = {"mto": mto, "mtype": mtype, "encrypted": encrypted}
        return self.encrypt_decision or encrypted is True

    async def _send_omemo_message(self, **kwargs):
        if self.encrypt_raises:
            raise self.encrypt_raises
        self.encrypted_sent.append(kwargs)
        return {"encrypted": kwargs}

    def send_message(self, **kwargs):
        self.sent.append(kwargs)
        return {"plain": kwargs}


@pytest.mark.asyncio
async def test_bot_send_message_plaintext_by_default():
    bot = MessagingBot()
    result = await bot.bot_send_message(mto="room@example.org", mbody="hello")
    assert result["plain"]["mbody"] == "hello"
    assert bot.sent
    assert not bot.encrypted_sent


@pytest.mark.asyncio
async def test_bot_send_message_uses_reply_encryption_context():
    bot = MessagingBot()
    token = bot._set_reply_encryption_context(True)
    try:
        result = await bot.bot_send_message(mto="room@example.org", mbody="secret")
    finally:
        bot._reset_reply_encryption_context(token)

    assert result["encrypted"]["mbody"] == "secret"
    assert bot.encrypted_sent
    assert not bot.sent


@pytest.mark.asyncio
async def test_bot_send_message_no_plaintext_leak_when_encryption_fails_without_fallback():
    bot = MessagingBot()
    bot.encrypt_decision = True
    bot.encrypt_raises = RuntimeError("no recipients")
    bot.omemo_plaintext_fallback = False

    result = await bot.bot_send_message(mto="room@example.org", mbody="secret")

    assert result is None
    assert not bot.sent


@pytest.mark.asyncio
async def test_bot_send_message_fallback_when_explicitly_enabled():
    bot = MessagingBot()
    bot.encrypt_decision = True
    bot.encrypt_raises = RuntimeError("no recipients")
    bot.omemo_plaintext_fallback = True

    result = await bot.bot_send_message(mto="room@example.org", mbody="secret")

    assert result["plain"]["mbody"] == "secret"
    assert bot.sent


@pytest.mark.asyncio
async def test_reply_encryption_context_does_not_leak_to_child_tasks():
    bot = MessagingBot()
    child_started = asyncio.Event()

    async def child_send():
        child_started.set()
        return await bot.bot_send_message(mto="room@example.org", mbody="background")

    token = bot._set_reply_encryption_context(True)
    try:
        child = asyncio.create_task(child_send())
        await asyncio.wait_for(child_started.wait(), timeout=1)
        foreground = await bot.bot_send_message(mto="admin@example.org", mbody="reply")
        background = await asyncio.wait_for(child, timeout=1)
    finally:
        bot._reset_reply_encryption_context(token)

    assert foreground["encrypted"]["mbody"] == "reply"
    assert background["plain"]["mbody"] == "background"
    assert [item["mbody"] for item in bot.encrypted_sent] == ["reply"]
    assert [item["mbody"] for item in bot.sent] == ["background"]


@pytest.mark.asyncio
async def test_durable_send_refuses_task_local_encrypted_reply():
    bot = MessagingBot()
    token = bot._set_reply_encryption_context(True)
    try:
        with pytest.raises(ValueError, match="task-local explicit encryption"):
            await bot.bot_send_message(mto="admin@example.org", mbody="secret", durable=True)
    finally:
        bot._reset_reply_encryption_context(token)
    assert not bot.sent
    assert not bot.encrypted_sent


@pytest.mark.asyncio
async def test_durable_plaintext_replay_uses_stable_origin_id():
    class CapturedStanza(dict):
        def __init__(self, **fields):
            super().__init__({**fields, "origin_id": {}})

        def send(self):
            return True

    class StanzaBot(MessagingBot):
        def make_message(self, **fields):
            self.stanza = CapturedStanza(**fields)
            return self.stanza

    bot = StanzaBot()
    result = await bot._send_message_transport(
        mto="room@example.org", mbody="notice", mtype="groupchat",
        encrypted=None, origin_id="persisted-id",
    )
    assert result is True
    assert bot.stanza["id"] == "persisted-id"
    assert bot.stanza["origin_id"]["id"] == "persisted-id"
    assert not bot.sent  # low-level send_message would create a different id


@pytest.mark.asyncio
async def test_durable_replay_refuses_to_lose_origin_id():
    bot = MessagingBot()  # No make_message implementation.
    with pytest.raises(RuntimeError, match="stable id"):
        await bot._send_message_transport(
            mto="room@example.org", mbody="notice", mtype="groupchat",
            encrypted=None, origin_id="persisted-id",
        )
    assert not bot.sent


@pytest.mark.asyncio
async def test_outbox_transport_false_is_retryable_not_acked():
    class Stanza:
        def __init__(self):
            self.fields = {"origin_id": {}}

        def __getitem__(self, key):
            return self.fields[key]

        def __setitem__(self, key, value):
            self.fields[key] = value

        def send(self):
            return False

    bot = MessagingBot()
    bot.make_message = lambda **_kwargs: Stanza()
    with pytest.raises(RuntimeError, match="rejected outbound stanza"):
        await bot._send_message_transport(
            mto="admin@example.org", mbody="alert", mtype="groupchat",
            encrypted=None, origin_id="persisted-123", raise_on_failure=True,
        )


@pytest.mark.asyncio
async def test_omemo_reset_pending_never_sends_queued_notice_as_plaintext():
    bot = MessagingBot()
    bot.omemo_reset_pending_restart = True
    result = await bot.bot_send_message(mto="alice@example.org", mbody="health",
                                        mtype="chat")
    assert result is False
    assert not bot.sent
    assert not bot.encrypted_sent


@pytest.mark.asyncio
async def test_omemo_reset_pending_explicit_plaintext_control_message_allowed():
    bot = MessagingBot()
    bot.omemo_reset_pending_restart = True
    result = await bot.bot_send_message(mto="room@example.org", mbody="reset done",
                                        mtype="groupchat", encrypted=False)
    assert result["plain"]["mbody"] == "reset done"
