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
