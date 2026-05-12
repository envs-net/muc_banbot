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

# Python logging level.
# Use DEBUG for detailed troubleshooting, INFO for normal operation.
# Common values: DEBUG, INFO, WARNING, ERROR, CRITICAL
# Can be changed at runtime via !reloadconfig.
LOG_LEVEL = "INFO"

COMMAND_PREFIX = "!" # Command prefix used to trigger bot commands in rooms
ANNOUNCE_STARTUP = True  # True = Show startup messages in the admin room, False = off
ANNOUNCE_SYNC_DETAILS = True  # True = Show detailed sync progress messages at startup (per-room details), False = off. Manual !sync and !syncbans commands always show details
SHOW_BAN_IN_MUC = False  # True = visible ban/kick in protected rooms, False = hidden
ALLOW_USER_COMMANDS_IN_PROTECTED_ROOMS = True  # True = Commands are enabled, False = deactivated

HEALTH_CHECK_INTERVAL = 300  # Interval (seconds) for health checks of bot rights in rooms. Minimum: 60. Default: 300
UNBAN_CHECK_INTERVAL = 60  # Interval (seconds) for checking expired temporary bans. Lower = faster unbans but more DB queries. Default: 60
MAX_TEMPBAN_DAYS = 30  # Maximum temporary ban duration in days (1-365). Default: 30

# Rate limit for public commands in protected rooms.
# Example default: max 3 uses per nick/room/command every 30 seconds.
PUBLIC_COMMAND_RATE_LIMIT_WINDOW = 30
PUBLIC_COMMAND_RATE_LIMIT_MAX = 3

# ================= PERFORMANCE TUNING =================

# Concurrency limit for MUC write operations (IQ stanzas to XMPP server)
# Each ban/unban operation = 1 IQ request. Higher = faster but more server load.
# Recommended: 5-20 (default: 5)
MUC_WRITE_SEMAPHORE = 5

# ================= LOGGING / AUDIT =================

# Structured event logs are emitted as JSON for important moderation events.
# Useful with journalctl, jq, Loki, ELK, etc.
STRUCTURED_EVENT_LOGS = True

# SQLite audit log for moderation actions and important operational events.
# Retention is capped at 365 days by config validation.
AUDIT_LOG_ENABLED = True
AUDIT_LOG_RETENTION_DAYS = 365

# ================= OMEMO ENCRYPTION =================

# Enable OMEMO-encrypted outgoing bot messages.
# Requires a restart when changed.
OMEMO_ENABLED = False

# JSON file used to store OMEMO identity, device state and sessions.
# The bot creates the parent directory with 0700 and the file with 0600.
# Keep this file persistent and private. Losing it creates a new OMEMO identity.
OMEMO_STORAGE_FILE = "data/omemo.json"

# Automatically encrypt bot-generated groupchat messages in protected rooms.
# This includes rooms added later via !room add because protected rooms are
# loaded from the bot database.
OMEMO_AUTO_ENCRYPT_PROTECTED_ROOMS = True

# Automatically encrypt bot-generated groupchat messages in the admin room.
# Disabled by default; add the admin room to OMEMO_ENCRYPTED_ROOMS if desired.
OMEMO_AUTO_ENCRYPT_ADMIN_ROOM = False

# Optional manual override: MUCs where bot-generated groupchat messages should
# always be OMEMO encrypted. Use ["*"] to encrypt all groupchat messages.
OMEMO_ENCRYPTED_ROOMS = []

# Optional manual override: MUCs that should always receive plaintext even when
# they are protected rooms or match OMEMO_ENCRYPTED_ROOMS.
OMEMO_PLAINTEXT_ROOMS = []

# False is safer: if encryption fails in an encrypted room, the bot will not
# leak the message as plaintext. Set True only if you explicitly want fallback.
OMEMO_PLAINTEXT_FALLBACK = False

# Encrypt direct/chat messages when encrypted=None in bot_send_message().
# The bot normally rejects DMs, so this is disabled by default.
OMEMO_ENCRYPT_DIRECT_MESSAGES = False

# Recipient discovery for encrypted MUC messages.
# Affiliations require enough room privileges; occupants require visible real JIDs.
OMEMO_INCLUDE_MUC_AFFILIATIONS = True
OMEMO_INCLUDE_MUC_OCCUPANTS = True
OMEMO_INCLUDE_OWN_DEVICES = True

# Seconds to wait for slixmpp-omemo initialization before failing an encrypted send.
OMEMO_READY_TIMEOUT = 15

# ================= RTBL (Real-Time Block List) =================

# Enable RTBL PubSub support.
# Subscriptions are managed via !rtbl add / !rtbl delete in the admin room.
# Requires a restart when changed.
RTBL_ENABLED = False

# True = announce RTBL bans (and skipped admin-protected entries) in the admin room.
# Can be changed at runtime via !reloadconfig.
RTBL_ANNOUNCE = True

# Periodically re-fetch all items from subscribed RTBL nodes.
# Acts as a fallback in case PubSub events were missed (e.g. after reconnect).
# Successful refreshes reconcile the local RTBL cache with the current PubSub node
# snapshot and unban stale issuer=rtbl bans when entries disappeared from the list.
# Set to 0 to disable. Default: 3600 (once per hour).
RTBL_REFRESH_INTERVAL = 3600

# ---- Own RTBL feed (others can subscribe to this node) ----
# Requires a PubSub service on your own XMPP server.
# Some servers (e.g., ejabberd, Prosody with mod_pubsub) offer pubsub.domain.tld.
# Requires a restart when changed.
RTBL_PUBLISH_ENABLED = False
RTBL_PUBLISH_SERVICE = "pubsub.domain.tld" # PubSub Service JID
RTBL_PUBLISH_JID_NODE = "muc_bans_sha256"      # Node for SHA-256 hashed JID bans
RTBL_PUBLISH_DOMAIN_NODE = "muc_bans_domains"  # Node for plaintext domain bans

# ================= UPDATE CHECK =================

VERSION_CHECK_ENABLED = False  # True = check periodically for new bot releases, False = disabled
VERSION_CHECK_INTERVAL = 3600  # Interval in seconds for update checks. Default: 3600 (1 hour)
# GitHub release URL. GitHub URLs are checked via the GitHub releases/latest API
# with a redirect-parser fallback.
VERSION_CHECK_URL = "https://github.com/envs-net/muc_banbot/releases/latest"
