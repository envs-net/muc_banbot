import pytest

from banbot.direct_messages import DirectMessageMixin
from banbot.utils import bare_jid


class FakeJid:
    def __init__(self, bare, resource=None):
        self.bare = bare
        self.resource = resource

    def __str__(self):
        return f"{self.bare}/{self.resource}" if self.resource else self.bare


class FakeDirectMessage:
    def __init__(self, *, bare, resource=None, msg_type="chat"):
        self._data = {"from": FakeJid(bare, resource), "type": msg_type}

    def __getitem__(self, key):
        return self._data[key]


class DirectBot(DirectMessageMixin):
    def __init__(self):
        self.boundjid = FakeJid("bot@example.org", "res")
        self.protected_rooms = {"room@conference.example.org"}
        self.occupants = {
            "admin@conference.envs.net": {
                "Admin": {"jid": "admin@example.org/res", "affiliation": "owner"}
            },
            "room@conference.example.org": {
                "Admin": {"jid": "admin@example.org/res", "affiliation": "member"},
                "User": {"jid": "user@example.org/res", "affiliation": "member"},
            },
        }
        self.sent = []

    def bare_jid(self, jid):
        return bare_jid(jid)

    async def bot_send_message(self, **kwargs):
        self.sent.append(kwargs)


@pytest.mark.asyncio
async def test_direct_message_ignores_own_messages():
    bot = DirectBot()
    await bot.on_direct_message(FakeDirectMessage(bare="bot@example.org"))
    assert bot.sent == []


@pytest.mark.asyncio
async def test_direct_message_rejects_regular_user_dm():
    bot = DirectBot()
    await bot.on_direct_message(FakeDirectMessage(bare="user@example.org"))
    assert bot.sent[0]["mto"] == "user@example.org"
    assert "only operate" in bot.sent[0]["mbody"]


@pytest.mark.asyncio
async def test_muc_pm_from_admin_gets_admin_hint():
    bot = DirectBot()
    await bot.on_direct_message(
        FakeDirectMessage(bare="room@conference.example.org", resource="Admin")
    )
    assert bot.sent[0]["mto"] == "room@conference.example.org/Admin"
    assert "Nice try, admin" in bot.sent[0]["mbody"]
