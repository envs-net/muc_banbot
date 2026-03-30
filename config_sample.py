# ================= CONFIG =================

JID = "adminbot@domain.tld"
PASSWORD = "yourpassword"
ADMIN_ROOM = "admin@muc.domain.tld"
NICK = "adminbot"
DB_FILE = "banbot.db"
AVATAR_PATH = "avatar.png" # PATH to Avatar, None = do not set an avatar

UNBAN_CHECK_INTERVAL = 60  # Interval (seconds) for checking expired temporary bans. Lower = faster unbans but more DB queries. Default: 60

ANNOUNCE_STARTUP = True  # True = Show startup messages in the admin room, False = off
SHOW_BAN_IN_MUC = False  # True = visible ban/kick in protected rooms, False = hidden
ALLOW_USER_COMMANDS_IN_PROTECTED_ROOMS = True  # True = Commands are enabled, False = deactivated
