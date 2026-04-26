# ================= CONFIG =================

DB_FILE = "banbot.db"

JID = "adminbot@domain.tld"
RESOURCE = "service"
PASSWORD = "yourpassword"
ADMIN_ROOM = "admin@muc.domain.tld"
NICK = "adminbot"

# ================= vCARD SETTINGS =================
AVATAR_PATH = "avatar.png"           # Path to avatar file (PNG/JPG), None disables avatar
VCARD_NICKNAME = "My Bot Nickname"   # Optional nickname for vCard
VCARD_FN = "Admin Bot"               # Full name
VCARD_ORG = "My Organization"        # Organization name
VCARD_ROLE = "Administrator"         # Role/position
VCARD_URL = "https://example.com"    # Website URL
VCARD_NOTE = "Bot Admin Assistant"   # Notes/description

# ================= BOT SETTINGS =================

COMMAND_PREFIX = "!" # Command prefix used to trigger bot commands in rooms
ANNOUNCE_STARTUP = True  # True = Show startup messages in the admin room, False = off
ANNOUNCE_SYNC_DETAILS = True  # True = Show detailed sync progress messages at startup (per-room details), False = off. Manual !sync and !syncbans commands always show details
SHOW_BAN_IN_MUC = False  # True = visible ban/kick in protected rooms, False = hidden
ALLOW_USER_COMMANDS_IN_PROTECTED_ROOMS = True  # True = Commands are enabled, False = deactivated

HEALTH_CHECK_INTERVAL = 300  # Interval (seconds) for health checks of bot rights in rooms. Minimum: 60. Default: 300
UNBAN_CHECK_INTERVAL = 60  # Interval (seconds) for checking expired temporary bans. Lower = faster unbans but more DB queries. Default: 60
MAX_TEMPBAN_DAYS = 30  # Maximum temporary ban duration in days (1-365). Default: 30

# ================= PERFORMANCE TUNING =================

# Concurrency limit for MUC write operations (IQ stanzas to XMPP server)
# Each ban/unban operation = 1 IQ request. Higher = faster but more server load.
# Recommended: 5-20 (default: 5)
MUC_WRITE_SEMAPHORE = 5

# ================= UPDATE CHECK =================

VERSION_CHECK_ENABLED = False  # True = check periodically for new bot releases, False = disabled
VERSION_CHECK_INTERVAL = 3600  # Interval in seconds for update checks. Default: 3600 (1 hour)
VERSION_CHECK_URL = "https://github.com/envs-net/muc_banbot/releases/latest"  # URL used to detect the latest GitHub release
