# ================= CONFIG =================

JID = "adminbot@domain.tld"
RESOURCE = "service"
PASSWORD = "yourpassword"
ADMIN_ROOM = "admin@muc.domain.tld"
NICK = "adminbot"

# ================= CONNECTION =================

# Optional connection host override. None uses the domain from JID.
CONNECT_HOST = None

# XMPP client-to-server port.
# 5222 = normal C2S with STARTTLS
# 5223 = direct TLS / legacy SSL
# 443  = direct TLS only if your server offers native XMPP there
CONNECT_PORT = 5222

# Direct TLS connection mode.
# False = normal STARTTLS on CONNECT_PORT, usually 5222
# True  = direct TLS, usually 5223
CONNECT_DIRECT_TLS = False

# ================= DATABASE / BACKUPS =================

DB_FILE = "banbot.db"

# Managed SQLite database backups.
# !backup creates snapshots, !backup list [all|page|last] shows them, and !restore can restore them.
DB_BACKUP_ON_START = True
DB_BACKUP_DIR = "data/backups"
DB_BACKUP_KEEP = 15
DB_BACKUP_INCLUDE_OMEMO = True

# ================= MANAGED CSV EXPORTS =================

EXPORT_DIR = "data/exports"
EXPORT_KEEP = 15

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

# Allow admins to use selected read-only commands via direct messages / MUC PMs.
# Mutating commands are still restricted to ADMIN_ROOM.
# Can be changed at runtime via !reloadconfig.
ALLOW_ADMIN_COMMANDS_IN_DMS = True

# Enable protected-room invite workflow. When enabled, incoming MUC invites are
# announced in the admin room and can be accepted/declined with !room invite.
# Can be changed at runtime via !reloadconfig.
ROOM_INVITES_ENABLED = False

# Pending room invites older than this many days are expired automatically.
# Set to 0 to keep pending invites indefinitely until accepted/declined/cleanup.
ROOM_INVITE_MAX_AGE_DAYS = 30

HEALTH_CHECK_INTERVAL = 300  # Interval (seconds) for health checks of bot rights in rooms. Minimum: 60. Default: 300
UNBAN_CHECK_INTERVAL = 60  # Interval (seconds) for checking expired temporary bans. Lower = faster unbans but more DB queries. Default: 60
MAX_TEMPBAN_DAYS = 30  # Maximum temporary ban duration in days (1-365). Default: 30

# Rate limit for public commands in protected rooms.
# Example default: max 3 uses per nick/room/command every 30 seconds.
PUBLIC_COMMAND_RATE_LIMIT_WINDOW = 30
PUBLIC_COMMAND_RATE_LIMIT_MAX = 3

# Default number of items shown per page by paginated list commands.
LIST_PAGE_SIZE = 10

# ================= PERFORMANCE TUNING =================

# Concurrency limit for MUC write operations (IQ stanzas to XMPP server)
# Each ban/unban operation = 1 IQ request. Higher = faster but more server load.
# Recommended: 5-20 (default: 5)
MUC_WRITE_SEMAPHORE = 5

# Number of rooms processed concurrently during full sync operations.
# Higher = faster sync, but more server/database load. Default: 10
SYNC_BATCH_SIZE = 10

# ================= LOGGING / AUDIT =================

# Structured event logs are emitted as JSON for important moderation events.
# Useful with journalctl, jq, Loki, ELK, etc.
STRUCTURED_EVENT_LOGS = True

# SQLite audit log for moderation actions and important operational events.
# Retention is capped at 365 days by config validation.
AUDIT_LOG_ENABLED = True
AUDIT_LOG_RETENTION_DAYS = 365

# ================= EVENT ALERTS =================

# Send operational alerts to ADMIN_ROOM. Alerts are deduplicated per type/window.
ALERT_ON_RECONNECT = True
ALERT_ON_ADMIN_RIGHTS_LOST = True
ALERT_ON_HEALTH_CHECK_FAILURE = True
ALERT_ON_DB_STATS_FAILURE = True
ALERT_ON_REDACTION_FAILURE = True

# 0 disables the DB size alert. Values are in MiB.
ALERT_ON_DB_SIZE_MB = 0

# Alert after this many consecutive periodic RTBL refresh failures per subscription.
# 0 disables RTBL refresh failure alerts.
ALERT_ON_RTBL_REFRESH_FAILURES = 3

# Suppress duplicate alerts with the same key for this many seconds. 0 disables deduplication.
ALERT_DEDUP_WINDOW = 300

# ================= OMEMO ENCRYPTION =================

# Enable OMEMO support for encrypted incoming commands and outgoing replies.
# Requires optional dependencies from requirements-omemo.txt and a restart when changed.
# If enabled without the optional dependencies, the bot starts with OMEMO disabled
# and logs a warning. Plaintext bot functionality is unaffected.
OMEMO_ENABLED = False

# JSON file used to store OMEMO identity, device state and sessions.
# The bot creates the parent directory with 0700 and the file with 0600.
# Keep this file persistent and private. Losing it creates a new OMEMO identity.
OMEMO_STORAGE_FILE = "data/omemo.json"

# Automatically encrypt proactive bot messages to the admin room when OMEMO is enabled.
# Incoming encrypted commands are always answered encrypted regardless of this setting.
OMEMO_AUTO_ENCRYPT_ADMIN_ROOM = True

# False is safer: if encryption is required and fails, the bot will not leak the
# message as plaintext. Set True only if you explicitly want fallback.
OMEMO_PLAINTEXT_FALLBACK = False

# Reset OMEMO storage automatically when JID, RESOURCE or NICK changes.
# The old storage is moved to a timestamped .bak-* file, never deleted.
OMEMO_RESET_ON_IDENTITY_CHANGE = True

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

# ================= VERSION CHECK =================

VERSION_CHECK_ENABLED = False  # True = check periodically for new bot releases, False = disabled
VERSION_CHECK_INTERVAL = 3600  # Interval in seconds for update checks. Default: 3600 (1 hour)
# GitHub release URL. GitHub URLs are checked via the GitHub releases/latest API
# with a redirect-parser fallback.
VERSION_CHECK_URL = "https://github.com/envs-net/muc_banbot/releases/latest"

# ================= REDACTION =================

# Optional protected-room message redaction support. When enabled, BanBot
# indexes room-assigned stanza IDs for messages it sees in protected rooms.
# Message bodies are not stored. Can be changed at runtime via !reloadconfig.
REDACTION_ENABLED = False

# How long to keep indexed stanza IDs. 0 = keep indefinitely.
REDACTION_INDEX_RETENTION_DAYS = 30

# Ban comments matching one of these strings trigger automatic redaction for
# JID bans. Matching is case-insensitive.
REDACTION_AUTO_REASONS = [
    "code of conduct violations",
    "open-reg",
    "spam",
    "advertising",
    "impersonation",
    "disagreement",
    "harassment",
    "hate speech",
    "doxxing",
    "violence",
    "terrorism",
    "csam",
    "gore",
    "troll",
    "racist",
    "cp",
    "nsfw",
]
