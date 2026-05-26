import pytest

from banbot.direct_messages import ADMIN_ROOM, DirectMessageMixin
from banbot.utils import bare_jid


class FakeJid:
    def __init__(self, bare, resource=None):
        self.bare = bare
        self.resource = resource

    def __str__(self):
        return f"{self.bare}/{self.resource}" if self.resource else self.bare


class FakeDirectMessage:
    def __init__(self, *, bare, resource=None, msg_type="chat", body=""):
        self._data = {"from": FakeJid(bare, resource), "type": msg_type, "body": body}

    def __getitem__(self, key):
        return self._data[key]


class DirectBot(DirectMessageMixin):
    def __init__(self):
        self.boundjid = FakeJid("bot@example.org", "res")
        self.protected_rooms = {"room@conference.example.org"}
        self.occupants = {
            ADMIN_ROOM: {
                "Admin": {"jid": "admin@example.org/res", "affiliation": "owner"}
            },
            "room@conference.example.org": {
                "Admin": {"jid": "admin@example.org/res", "affiliation": "member"},
                "User": {"jid": "user@example.org/res", "affiliation": "member"},
            },
        }
        self.sent = []
        self.command_prefix = "!"
        self.calls = []

    def bare_jid(self, jid):
        return bare_jid(jid)

    async def bot_send_message(self, **kwargs):
        self.sent.append(kwargs)

    async def _cmd_config(self, room):
        self.calls.append(("config", room))
        await self.bot_send_message(mto=room, mbody="config output", mtype="groupchat")

    async def _cmd_status(self, room):
        self.calls.append(("status", room))
        await self.bot_send_message(mto=room, mbody="status output", mtype="groupchat")

    async def cmd_banlist(self, room, page=1, show_all=False):
        self.calls.append(("banlist", room, page, show_all))
        await self.bot_send_message(mto=room, mbody="banlist output", mtype="groupchat")

    async def cmd_banlist_rtbl(self, room, page=1, show_all=False):
        self.calls.append(("banlist_rtbl", room, page, show_all))
        await self.bot_send_message(mto=room, mbody="rtbl banlist output", mtype="groupchat")

    async def cmd_room(self, args, room):
        self.calls.append(("room", tuple(args), room))
        await self.bot_send_message(mto=room, mbody="room output", mtype="groupchat")

    async def cmd_ignore(self, args, room, actor="unknown", command_name="ignore"):
        self.calls.append(("ignore", tuple(args), room, actor, command_name))
        await self.bot_send_message(mto=room, mbody="ignore output", mtype="groupchat")

    async def cmd_rtbl(self, args, room, actor="unknown"):
        self.calls.append(("rtbl", tuple(args), room, actor))
        await self.bot_send_message(mto=room, mbody="rtbl output", mtype="groupchat")

    async def cmd_audit(self, args, room):
        self.calls.append(("audit", tuple(args), room))
        await self.bot_send_message(mto=room, mbody="audit output", mtype="groupchat")


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
    assert "Admin DM support is read-only" in bot.sent[0]["mbody"]
    assert ADMIN_ROOM in bot.sent[0]["mbody"]


@pytest.mark.parametrize("affiliation", ["admin", "owner"])
@pytest.mark.asyncio
async def test_muc_pm_from_protected_room_admin_or_owner_gets_admin_hint(affiliation):
    bot = DirectBot()
    bot.occupants["room@conference.example.org"]["RoomAdmin"] = {
        "jid": "roomadmin@example.org/res",
        "affiliation": affiliation,
    }

    await bot.on_direct_message(
        FakeDirectMessage(bare="room@conference.example.org", resource="RoomAdmin")
    )

    assert bot.sent[0]["mto"] == "room@conference.example.org/RoomAdmin"
    assert "Admin DM support is read-only" in bot.sent[0]["mbody"]
    assert ADMIN_ROOM in bot.sent[0]["mbody"]


@pytest.mark.asyncio
async def test_direct_message_ignores_groupchat_messages():
    bot = DirectBot()
    await bot.on_direct_message(FakeDirectMessage(bare="user@example.org", msg_type="groupchat"))
    assert bot.sent == []


@pytest.mark.asyncio
async def test_regular_dm_from_admin_gets_admin_room_hint():
    bot = DirectBot()
    await bot.on_direct_message(FakeDirectMessage(bare="admin@example.org", resource="laptop"))
    assert bot.sent[0]["mto"] == "admin@example.org"
    assert "Admin DM support is read-only" in bot.sent[0]["mbody"]
    assert ADMIN_ROOM in bot.sent[0]["mbody"]


@pytest.mark.asyncio
async def test_muc_pm_admin_detection_falls_back_to_admin_room_real_jid():
    bot = DirectBot()
    await bot.on_direct_message(
        FakeDirectMessage(bare="room@conference.example.org", resource="Admin")
    )
    # Admin is only a member in the protected room, but their real JID is owner
    # in ADMIN_ROOM, so the fallback should treat the MUC PM as admin.
    assert bot.sent[0]["mto"] == "room@conference.example.org/Admin"
    assert "Admin DM support is read-only" in bot.sent[0]["mbody"]


@pytest.mark.asyncio
async def test_muc_pm_from_regular_user_gets_rejection():
    bot = DirectBot()
    await bot.on_direct_message(
        FakeDirectMessage(bare="room@conference.example.org", resource="User")
    )
    assert bot.sent[0]["mto"] == "room@conference.example.org/User"
    assert "ban management bot" in bot.sent[0]["mbody"]
    assert "only listen to admins" in bot.sent[0]["mbody"]


@pytest.mark.asyncio
async def test_admin_dm_can_use_config_readonly_command():
    bot = DirectBot()
    await bot.on_direct_message(FakeDirectMessage(bare="admin@example.org", resource="laptop", body="!config"))

    assert bot.calls == [("config", "admin@example.org")]
    assert len(bot.sent) == 1
    assert bot.sent[0] == {"mto": "admin@example.org", "mbody": "config output", "mtype": "chat"}


@pytest.mark.asyncio
async def test_admin_dm_can_use_status_readonly_command():
    bot = DirectBot()
    await bot.on_direct_message(FakeDirectMessage(bare="admin@example.org", resource="laptop", body="!status"))

    assert bot.calls == [("status", "admin@example.org")]
    assert len(bot.sent) == 1
    assert bot.sent[0] == {"mto": "admin@example.org", "mbody": "status output", "mtype": "chat"}


@pytest.mark.asyncio
async def test_admin_dm_can_use_banlist_and_rtbl_banlist():
    bot = DirectBot()
    await bot.on_direct_message(FakeDirectMessage(bare="admin@example.org", resource="laptop", body="!banlist all"))
    await bot.on_direct_message(FakeDirectMessage(bare="admin@example.org", resource="laptop", body="!blacklist rtbl last"))
    await bot.on_direct_message(FakeDirectMessage(bare="admin@example.org", resource="laptop", body="!banlist rtbl all"))

    assert bot.calls[0] == ("banlist", ADMIN_ROOM, 1, True)
    assert bot.calls[1] == ("banlist_rtbl", ADMIN_ROOM, -1, False)
    assert bot.calls[2] == ("banlist_rtbl", ADMIN_ROOM, 1, True)
    assert len(bot.sent) == 3
    assert all(sent["mtype"] == "chat" for sent in bot.sent)


@pytest.mark.asyncio
async def test_admin_dm_can_use_room_and_invite_lists():
    bot = DirectBot()
    await bot.on_direct_message(FakeDirectMessage(bare="admin@example.org", resource="laptop", body="!room list all"))
    await bot.on_direct_message(FakeDirectMessage(bare="admin@example.org", resource="laptop", body="!room invite list last"))

    assert bot.calls[0] == ("room", ("list", "all"), "admin@example.org")
    assert bot.calls[1] == ("room", ("invite", "list", "last"), "admin@example.org")
    assert all(sent["mtype"] == "chat" for sent in bot.sent)


@pytest.mark.asyncio
async def test_admin_dm_rejects_mutating_room_commands():
    bot = DirectBot()
    await bot.on_direct_message(FakeDirectMessage(bare="admin@example.org", resource="laptop", body="!room add test@conference.example.org"))

    assert bot.calls == []
    assert "read-only" in bot.sent[-1]["mbody"]
    assert bot.sent[-1]["mtype"] == "chat"


@pytest.mark.asyncio
async def test_admin_dm_can_use_ignore_whitelist_rtbl_and_audit_lists():
    bot = DirectBot()
    await bot.on_direct_message(FakeDirectMessage(bare="admin@example.org", resource="laptop", body="!ignore list all"))
    await bot.on_direct_message(FakeDirectMessage(bare="admin@example.org", resource="laptop", body="!whitelist last"))
    await bot.on_direct_message(FakeDirectMessage(bare="admin@example.org", resource="laptop", body="!rtbl list"))
    await bot.on_direct_message(FakeDirectMessage(bare="admin@example.org", resource="laptop", body="!audit last"))

    assert bot.calls[0] == ("ignore", ("list", "all"), "admin@example.org", "admin@example.org", "ignore")
    assert bot.calls[1] == ("ignore", ("last",), "admin@example.org", "admin@example.org", "whitelist")
    assert bot.calls[2] == ("rtbl", ("list",), "admin@example.org", "admin@example.org")
    assert bot.calls[3] == ("audit", ("last",), "admin@example.org")
    assert all(sent["mtype"] == "chat" for sent in bot.sent)


@pytest.mark.asyncio
async def test_admin_dm_rejects_mutating_commands():
    bot = DirectBot()
    await bot.on_direct_message(FakeDirectMessage(bare="admin@example.org", resource="laptop", body="!ban bad@example.org"))

    assert bot.calls == []
    assert "read-only" in bot.sent[-1]["mbody"]
    assert ADMIN_ROOM in bot.sent[-1]["mbody"]


@pytest.mark.asyncio
async def test_muc_pm_admin_can_use_status_readonly_command():
    bot = DirectBot()
    await bot.on_direct_message(
        FakeDirectMessage(bare="room@conference.example.org", resource="Admin", body="!status")
    )

    assert bot.calls == [("status", "room@conference.example.org/Admin")]
    assert bot.sent[-1]["mto"] == "room@conference.example.org/Admin"
    assert bot.sent[-1]["mtype"] == "chat"
