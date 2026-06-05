import pytest
from typing import NamedTuple

from banbot.direct_messages import ADMIN_ROOM, DirectMessageMixin
from banbot.utils import bare_jid


LAST_PAGE_MARKER = -1


class UpdateResult(NamedTuple):
    has_update: bool
    latest_version: str
    error_message: str | None


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
        self.allow_admin_commands_in_dms = True
        self.calls = []
        self.version_check_url = "https://github.com/envs-net/muc_banbot/releases/latest"
        self.update_result = UpdateResult(False, "2.3.0", None)

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

    async def check_for_updates_once(self, announce=False):
        self.calls.append(("checkupdate", announce))
        return self.update_result

    async def cmd_banlist(self, room, page=1, show_all=False):
        self.calls.append(("banlist", room, page, show_all))
        await self.bot_send_message(mto=room, mbody="banlist output", mtype="groupchat")

    async def cmd_banlist_rtbl(self, room, page=1, show_all=False):
        self.calls.append(("banlist_rtbl", room, page, show_all))
        await self.bot_send_message(mto=room, mbody="rtbl banlist output", mtype="groupchat")

    async def cmd_bansearch(self, query, page=1, show_all=False):
        self.calls.append(("bansearch", query, page, show_all))
        await self.bot_send_message(mto=ADMIN_ROOM, mbody="bansearch output", mtype="groupchat")

    async def cmd_why(self, identifier, room):
        self.calls.append(("why", identifier, room))
        await self.bot_send_message(mto=room, mbody="why output", mtype="groupchat")

    async def cmd_room(self, args, room):
        self.calls.append(("room", tuple(args), room))
        await self.bot_send_message(mto=room, mbody="room output", mtype="groupchat")

    async def cmd_ignore(self, args, room, actor="unknown", command_name="ignore"):
        self.calls.append(("ignore", tuple(args), room, actor, command_name))
        await self.bot_send_message(mto=room, mbody="ignore output", mtype="groupchat")

    async def cmd_rtbl(self, args, room, actor="unknown"):
        self.calls.append(("rtbl", tuple(args), room, actor))
        await self.bot_send_message(mto=room, mbody="rtbl output", mtype="groupchat")

    async def cmd_omemo(self, args, room, actor=None):
        self.calls.append(("omemo", tuple(args), room, actor))
        await self.bot_send_message(mto=room, mbody="omemo output", mtype="groupchat")

    async def cmd_audit(self, args, room):
        self.calls.append(("audit", tuple(args), room))
        await self.bot_send_message(mto=room, mbody="audit output", mtype="groupchat")


@pytest.mark.asyncio
async def test_protected_room_command_behavior():
    bot = DirectBot()
    await bot.on_direct_message(
        FakeDirectMessage(
            bare="room@conference.example.org",
            resource="Admin",
            body="!config",
        )
    )

    assert bot.calls == [("config", "room@conference.example.org/Admin")]
    assert bot.sent[-1]["mto"] == "room@conference.example.org/Admin"
    assert bot.sent[-1]["mtype"] == "chat"


@pytest.mark.asyncio
async def test_non_protected_room_command_behavior():
    bot = DirectBot()
    await bot.on_direct_message(
        FakeDirectMessage(
            bare="other@conference.example.org",
            resource="Admin",
            body="!config",
        )
    )

    assert bot.calls == []
    assert bot.sent[-1]["mto"] == "other@conference.example.org"
    assert "only listen to admins" in bot.sent[-1]["mbody"]
    assert bot.sent[-1]["mtype"] == "chat"


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
    bot.occupants["room@conference.example.org"]["Admin"]["affiliation"] = "admin"

    await bot.on_direct_message(
        FakeDirectMessage(bare="room@conference.example.org", resource="Admin")
    )
    assert bot.sent[0]["mto"] == "room@conference.example.org/Admin"
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
    assert bot.sent[-1]["mto"] == "admin@example.org"
    assert bot.sent[-1]["mtype"] == "chat"
    assert bot.sent[-1]["mbody"] == "config output"


@pytest.mark.asyncio
async def test_admin_dm_rejects_mutating_config_commands():
    bot = DirectBot()
    await bot.on_direct_message(
        FakeDirectMessage(
            bare="admin@example.org",
            resource="laptop",
            body="!config set LOG_LEVEL DEBUG",
        )
    )

    assert bot.calls == []
    assert "config commands are read-only" in bot.sent[-1]["mbody"]
    assert bot.sent[-1]["mtype"] == "chat"


@pytest.mark.asyncio
async def test_admin_dm_can_use_omemo_readonly_commands():
    bot = DirectBot()
    await bot.on_direct_message(FakeDirectMessage(bare="admin@example.org", resource="laptop", body="!omemo status"))
    await bot.on_direct_message(FakeDirectMessage(bare="admin@example.org", resource="laptop", body="!omemo help"))

    assert bot.calls == [
        ("omemo", ("status",), "admin@example.org", "admin@example.org"),
        ("omemo", ("help",), "admin@example.org", "admin@example.org"),
    ]
    assert all(sent["mtype"] == "chat" for sent in bot.sent)


@pytest.mark.asyncio
async def test_admin_dm_can_use_status_readonly_command():
    bot = DirectBot()
    await bot.on_direct_message(FakeDirectMessage(bare="admin@example.org", resource="laptop", body="!status"))

    assert bot.calls == [("status", "admin@example.org")]
    assert bot.sent[-1]["mtype"] == "chat"
    assert bot.sent[-1]["mbody"] == "status output"


@pytest.mark.asyncio
async def test_admin_dm_can_use_updatecheck_aliases():
    bot = DirectBot()
    await bot.on_direct_message(FakeDirectMessage(bare="admin@example.org", resource="laptop", body="!checkupdate"))
    await bot.on_direct_message(FakeDirectMessage(bare="admin@example.org", resource="laptop", body="!updatecheck"))

    assert bot.calls == [("checkupdate", False), ("checkupdate", False)]
    assert bot.sent[-2]["mtype"] == "chat"
    assert bot.sent[-1]["mtype"] == "chat"
    assert "Bot is up to date" in bot.sent[-1]["mbody"]


@pytest.mark.asyncio
async def test_admin_dm_updatecheck_announces_available_update_with_release_url():
    bot = DirectBot()
    bot.update_result = UpdateResult(True, "2.4.0", None)

    await bot.on_direct_message(
        FakeDirectMessage(
            bare="admin@example.org",
            resource="laptop",
            body="!checkupdate",
        )
    )

    assert bot.calls == [("checkupdate", False)]
    assert bot.sent[-1]["mtype"] == "chat"
    assert "2.4.0" in bot.sent[-1]["mbody"]
    assert bot.version_check_url in bot.sent[-1]["mbody"]


@pytest.mark.asyncio
async def test_admin_dm_updatecheck_reports_errors():
    bot = DirectBot()
    bot.update_result = UpdateResult(False, "2.3.0", "network timeout")

    await bot.on_direct_message(
        FakeDirectMessage(
            bare="admin@example.org",
            resource="laptop",
            body="!checkupdate",
        )
    )

    assert bot.calls == [("checkupdate", False)]
    assert bot.sent[-1]["mtype"] == "chat"
    assert "Update check failed" in bot.sent[-1]["mbody"]
    assert "network timeout" in bot.sent[-1]["mbody"]


@pytest.mark.asyncio
async def test_admin_dm_can_use_bansearch_readonly_command():
    bot = DirectBot()
    await bot.on_direct_message(
        FakeDirectMessage(bare="admin@example.org", resource="laptop", body="!bansearch all spam wave")
    )
    await bot.on_direct_message(
        FakeDirectMessage(bare="admin@example.org", resource="laptop", body="!bansearch spam wave last")
    )

    assert bot.calls[0] == ("bansearch", "spam wave", 1, True)
    assert bot.calls[1] == ("bansearch", "spam wave", LAST_PAGE_MARKER, False)
    assert all(sent["mtype"] == "chat" for sent in bot.sent)


@pytest.mark.asyncio
async def test_admin_dm_bansearch_accepts_numeric_page():
    bot = DirectBot()

    await bot.on_direct_message(
        FakeDirectMessage(
            bare="admin@example.org",
            resource="laptop",
            body="!bansearch spam wave 2",
        )
    )

    assert bot.calls == [("bansearch", "spam wave", 2, False)]
    assert bot.sent[-1]["mtype"] == "chat"


@pytest.mark.asyncio
async def test_admin_dm_bansearch_requires_query():
    bot = DirectBot()
    await bot.on_direct_message(FakeDirectMessage(bare="admin@example.org", resource="laptop", body="!bansearch all"))

    assert bot.calls == []
    assert "Usage: !bansearch" in bot.sent[-1]["mbody"]
    assert bot.sent[-1]["mtype"] == "chat"


@pytest.mark.asyncio
async def test_admin_dm_bansearch_requires_arguments():
    bot = DirectBot()

    await bot.on_direct_message(
        FakeDirectMessage(
            bare="admin@example.org",
            resource="laptop",
            body="!bansearch",
        )
    )

    assert bot.calls == []
    assert "Usage:" in bot.sent[-1]["mbody"]
    assert "bansearch" in bot.sent[-1]["mbody"]
    assert bot.sent[-1]["mtype"] == "chat"


@pytest.mark.asyncio
async def test_admin_dm_can_use_why_readonly_command():
    bot = DirectBot()
    await bot.on_direct_message(
        FakeDirectMessage(bare="admin@example.org", resource="laptop", body="!why alice")
    )

    assert bot.calls == [("why", "alice", "admin@example.org")]
    assert bot.sent[-1]["mto"] == "admin@example.org"
    assert bot.sent[-1]["mtype"] == "chat"
    assert bot.sent[-1]["mbody"] == "why output"


@pytest.mark.asyncio
async def test_admin_dm_why_requires_target():
    bot = DirectBot()
    await bot.on_direct_message(
        FakeDirectMessage(bare="admin@example.org", resource="laptop", body="!why")
    )

    assert bot.calls == []
    assert "Usage: !why" in bot.sent[-1]["mbody"]
    assert bot.sent[-1]["mtype"] == "chat"


@pytest.mark.asyncio
async def test_admin_dm_can_use_banlist_and_rtbl_banlist():
    bot = DirectBot()
    await bot.on_direct_message(
        FakeDirectMessage(
            bare="admin@example.org",
            resource="laptop",
            body="!banlist all",
        )
    )
    await bot.on_direct_message(
        FakeDirectMessage(
            bare="admin@example.org",
            resource="laptop",
            body="!banlist rtbl last",
        )
    )

    assert bot.calls[0] == ("banlist", "admin@example.org", 1, True)
    assert bot.calls[1] == ("banlist_rtbl", "admin@example.org", LAST_PAGE_MARKER, False)
    assert all(sent["mtype"] == "chat" for sent in bot.sent)


@pytest.mark.asyncio
async def test_admin_dm_banlist_rejects_invalid_page_argument():
    bot = DirectBot()

    await bot.on_direct_message(
        FakeDirectMessage(
            bare="admin@example.org",
            resource="laptop",
            body="!banlist notanumber",
        )
    )

    assert bot.calls == []
    assert "Usage:" in bot.sent[-1]["mbody"]
    assert "banlist" in bot.sent[-1]["mbody"]
    assert bot.sent[-1]["mtype"] == "chat"


@pytest.mark.asyncio
async def test_admin_dm_rtbl_banlist_rejects_invalid_page_argument():
    bot = DirectBot()

    await bot.on_direct_message(
        FakeDirectMessage(
            bare="admin@example.org",
            resource="laptop",
            body="!banlist rtbl notanumber",
        )
    )

    assert bot.calls == []
    assert "Usage:" in bot.sent[-1]["mbody"]
    assert "banlist" in bot.sent[-1]["mbody"]
    assert bot.sent[-1]["mtype"] == "chat"


@pytest.mark.asyncio
async def test_admin_dm_can_use_room_and_invite_lists():
    bot = DirectBot()
    await bot.on_direct_message(FakeDirectMessage(bare="admin@example.org", resource="laptop", body="!room list all"))
    await bot.on_direct_message(FakeDirectMessage(bare="admin@example.org", resource="laptop", body="!room invite list last"))

    assert bot.calls[0] == ("room", ("list", "all"), "admin@example.org")
    assert bot.calls[1] == ("room", ("invite", "list", "last"), "admin@example.org")
    assert all(sent["mtype"] == "chat" for sent in bot.sent)


@pytest.mark.asyncio
async def test_admin_dm_rejects_invalid_room_list_page_argument():
    bot = DirectBot()

    await bot.on_direct_message(
        FakeDirectMessage(
            bare="admin@example.org",
            resource="laptop",
            body="!room list nope",
        )
    )

    assert bot.calls == []
    assert "Usage: !room list [all|page|last]" in bot.sent[-1]["mbody"]
    assert bot.sent[-1]["mtype"] == "chat"


@pytest.mark.asyncio
async def test_admin_dm_rejects_invalid_room_invite_list_page_argument():
    bot = DirectBot()

    await bot.on_direct_message(
        FakeDirectMessage(
            bare="admin@example.org",
            resource="laptop",
            body="!room invite list nope",
        )
    )

    assert bot.calls == []
    assert "Usage: !room invite list [all|page|last]" in bot.sent[-1]["mbody"]
    assert bot.sent[-1]["mtype"] == "chat"


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
    await bot.on_direct_message(FakeDirectMessage(bare="admin@example.org", resource="laptop", body="!rtbl list last"))
    await bot.on_direct_message(FakeDirectMessage(bare="admin@example.org", resource="laptop", body="!audit last"))

    assert bot.calls[0] == ("ignore", ("list", "all"), "admin@example.org", "admin@example.org", "ignore")
    assert bot.calls[1] == ("ignore", ("last",), "admin@example.org", "admin@example.org", "whitelist")
    assert bot.calls[2] == ("rtbl", ("list", "last"), "admin@example.org", "admin@example.org")
    assert bot.calls[3] == ("audit", ("last",), "admin@example.org")
    assert all(sent["mtype"] == "chat" for sent in bot.sent)


@pytest.mark.asyncio
async def test_admin_dm_rejects_invalid_rtbl_list_page_argument():
    bot = DirectBot()
    await bot.on_direct_message(FakeDirectMessage(bare="admin@example.org", resource="laptop", body="!rtbl list nope"))

    assert bot.calls == []
    assert "Usage: !rtbl list [all|page|last]" in bot.sent[-1]["mbody"]
    assert bot.sent[-1]["mtype"] == "chat"


@pytest.mark.asyncio
async def test_admin_dm_rejects_mutating_commands():
    bot = DirectBot()
    await bot.on_direct_message(FakeDirectMessage(bare="admin@example.org", resource="laptop", body="!ban bad@example.org"))

    assert bot.calls == []
    assert "read-only" in bot.sent[-1]["mbody"]
    assert ADMIN_ROOM in bot.sent[-1]["mbody"]


@pytest.mark.asyncio
async def test_admin_dm_rejects_mutating_ignore_and_whitelist_commands():
    bot = DirectBot()

    await bot.on_direct_message(
        FakeDirectMessage(
            bare="admin@example.org",
            resource="laptop",
            body="!ignore add user@example.org",
        )
    )

    await bot.on_direct_message(
        FakeDirectMessage(
            bare="admin@example.org",
            resource="laptop",
            body="!whitelist add user@example.org",
        )
    )

    assert bot.calls == []
    assert "read-only" in bot.sent[-2]["mbody"]
    assert bot.sent[-2]["mtype"] == "chat"
    assert "read-only" in bot.sent[-1]["mbody"]
    assert bot.sent[-1]["mtype"] == "chat"


@pytest.mark.asyncio
async def test_admin_dm_rejects_invalid_ignore_list_page_argument():
    bot = DirectBot()

    await bot.on_direct_message(
        FakeDirectMessage(
            bare="admin@example.org",
            resource="laptop",
            body="!ignore list nope",
        )
    )

    assert bot.calls == []
    assert "Usage: !ignore list [all|page|last]" in bot.sent[-1]["mbody"]
    assert bot.sent[-1]["mtype"] == "chat"


@pytest.mark.asyncio
async def test_admin_dm_rejects_invalid_whitelist_shortcut_page_argument():
    bot = DirectBot()

    await bot.on_direct_message(
        FakeDirectMessage(
            bare="admin@example.org",
            resource="laptop",
            body="!whitelist nope",
        )
    )

    assert bot.calls == []
    assert "read-only" in bot.sent[-1]["mbody"]
    assert bot.sent[-1]["mtype"] == "chat"


@pytest.mark.asyncio
async def test_admin_dm_rejects_non_list_rtbl_commands():
    bot = DirectBot()

    await bot.on_direct_message(
        FakeDirectMessage(
            bare="admin@example.org",
            resource="laptop",
            body="!rtbl add bad@example.org",
        )
    )

    assert bot.calls == []
    assert "read-only" in bot.sent[-1]["mbody"]
    assert bot.sent[-1]["mtype"] == "chat"


@pytest.mark.asyncio
async def test_muc_pm_admin_can_use_status_readonly_command():
    bot = DirectBot()
    await bot.on_direct_message(
        FakeDirectMessage(bare="room@conference.example.org", resource="Admin", body="!status")
    )

    assert bot.calls == [("status", "room@conference.example.org/Admin")]
    assert bot.sent[-1]["mto"] == "room@conference.example.org/Admin"
    assert bot.sent[-1]["mtype"] == "chat"


@pytest.mark.asyncio
async def test_admin_dm_commands_can_be_disabled():
    bot = DirectBot()
    bot.allow_admin_commands_in_dms = False

    await bot.on_direct_message(
        FakeDirectMessage(bare="admin@example.org", resource="laptop", body="!status")
    )

    assert bot.calls == []
    assert bot.sent[-1]["mto"] == "admin@example.org"
    assert bot.sent[-1]["mtype"] == "chat"
    body = bot.sent[-1]["mbody"]
    assert "admin room" in body
    assert ADMIN_ROOM in body


@pytest.mark.asyncio
async def test_admin_muc_pm_commands_can_be_disabled():
    bot = DirectBot()
    bot.allow_admin_commands_in_dms = False

    await bot.on_direct_message(
        FakeDirectMessage(bare="room@conference.example.org", resource="Admin", body="!status")
    )

    assert bot.calls == []
    assert bot.sent[-1]["mto"] == "room@conference.example.org/Admin"
    assert bot.sent[-1]["mtype"] == "chat"
    body = bot.sent[-1]["mbody"]
    assert "admin room" in body
    assert ADMIN_ROOM in body
