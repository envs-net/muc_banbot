# ================= CONFIG =================

JID = "adminbot@domain.tld"
PASSWORD = "yourpassword"
RESSOURCE = "service"
ADMIN_ROOM = "admin@muc.domain.tld"
NICK = "adminbot"
DB_FILE = "banbot.db"
AVATAR_PATH = "avatar.png" # PATH to Avatar, None = do not set an avatar

# ================= vCARD SETTINGS =================
VCARD_NICKNAME = "My Bot Nickname"   # Optional nickname for vCard
VCARD_FN = "Admin Bot"               # Full name
VCARD_ORG = "My Organization"        # Organization name
VCARD_ROLE = "Administrator"         # Role/position
VCARD_URL = "https://example.com"    # Website URL
VCARD_NOTE = "Bot Admin Assistant"   # Notes/description

# ================= BOT SETTINGS =================

ANNOUNCE_STARTUP = True  # True = Show startup messages in the admin room, False = off
SHOW_BAN_IN_MUC = False  # True = visible ban/kick in protected rooms, False = hidden
ALLOW_USER_COMMANDS_IN_PROTECTED_ROOMS = True  # True = Commands are enabled, False = deactivated
HEALTH_CHECK_INTERVAL = 300  # Interval (seconds) for health checks of bot rights in rooms. Minimum: 60. Default: 300
UNBAN_CHECK_INTERVAL = 60  # Interval (seconds) for checking expired temporary bans. Lower = faster unbans but more DB queries. Default: 60
MAX_TEMPBAN_DAYS = 30  # Maximum temporary ban duration in days (1-365). Default: 30
