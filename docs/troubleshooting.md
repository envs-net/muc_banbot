# Troubleshooting

## Invalid JID format

```text
❌ Invalid JID format: user@. Expected: user@domain.tld
```

Use a valid bare JID:

* `user@example.org`
* `alice@chat.example.net`

Avoid:

* `user@`
* `@example.org`
* `not a jid`

## Domain ban too generic

```text
❌ Domain '*.com' is too generic. Specify more precise domain (e.g., *.domain.tld).
```

Use specific wildcard domains:

```text
!ban *.spam-domain.example
!ban *.evil-company.co.uk
```

Do not use generic TLDs:

```text
!ban *.com
!ban *.org
```

## Tempban duration exceeds limit

```text
❌ Tempban duration exceeds MAX_TEMPBAN_DAYS (30 days). Max: 30 days.
```

Use a shorter duration or adjust `MAX_TEMPBAN_DAYS` in `config.py`.

## Bot loses admin rights

BanBot reports this to the admin room.

To fix:

1. Ensure the bot account has admin/owner affiliation in the room.
2. Check room configuration on the server.
3. Run `!sync` and `!status`.

## Ban not enforced

Run:

```text
!syncbans
```

This checks existing room outcasts, adopts orphan bans into the DB, and reapplies missing active bans.

## Tempbans do not expire

Check:

* `UNBAN_CHECK_INTERVAL` is valid
* The unban worker is running (`!status`)
* The bot still has admin/owner rights in protected rooms

## Health check warnings

Health warnings usually mean:

* The bot is missing from a room occupant list
* The bot lost admin/owner rights
* A background worker is not running
* The DB/status check failed

Run:

```text
!status
!sync
```

## Public policy does not show

Check:

* `ALLOW_USER_COMMANDS_IN_PROTECTED_ROOMS=True`
* `!policy show` in the admin room
* `!policy set <text>` if no text is configured
* `!policy enable`

Use literal `\n` for line breaks in `!policy set`.

## RTBL subscription cannot be added

Check:

* Service looks like a PubSub service/domain, e.g. `xmppbl.org` or `pubsub.example.org`
* Node name has no spaces
* Remote service is reachable
* The bot is not trying to subscribe to its own publish node

## RTBL fetch returns only 100 items

Some RTBL nodes intentionally retain only a rolling window of recent active items. If the provider confirms only the last 100 are retained, then those are all current entries.

BanBot treats successful refreshes as snapshots and removes stale local entries only after a successful fetch. Failed or suspicious pagination skips stale cleanup.

## RTBL publish node create/configure is forbidden

Create/configure nodes manually with Prosody shell commands. See [Prosody PubSub Setup](pubsub-setup.md).

`forbidden` during node configuration can be informational if publishing itself still succeeds.

## RTBL bans not applied

Check:

* `RTBL_ENABLED=True`
* `!rtbl list` shows subscriptions and item counts
* The user is not protected by an exact JID ignorelist entry
* The user's domain is not protected by a domain ignorelist entry for domain matches
* The user is not an admin/owner
* Startup/new subscription fetches scan current occupants; periodic refreshes do not rescan unchanged lists


## OMEMO optional dependency installation fails

`slixmpp-omemo` is optional. Plaintext BanBot operation only needs `requirements.txt`. Install OMEMO support only when encrypted command/reply support is needed.

If installing OMEMO fails with missing native headers or build errors, install the system libraries first. Raspbian example:

```bash
sudo apt install libsodium-dev libxeddsa-dev
pip install -r requirements-omemo.txt
```

If `OMEMO_ENABLED=True` but the optional OMEMO dependencies are not installed, BanBot starts with OMEMO disabled and logs a warning. Set `OMEMO_ENABLED=False` or install the optional dependencies to remove the warning.

## OMEMO bundle warnings

Broken, empty, or inaccessible OMEMO bundles are common in public MUCs. BanBot skips unusable recipients and suppresses noisy dependency warnings at normal INFO logging. Use `LOG_LEVEL="DEBUG"` to debug OMEMO dependencies.

## OMEMO reply is not encrypted

Check:

* `OMEMO_ENABLED=True`
* The incoming command was actually OMEMO-encrypted. Plaintext commands intentionally receive plaintext replies.
* The bot's OMEMO storage file is writable by the bot user.
* At least one current MUC occupant has a visible real JID and a usable OMEMO device.
* `OMEMO_PLAINTEXT_FALLBACK=False` means failed encrypted replies are not resent as plaintext.

## OMEMO storage permission problems

The OMEMO storage file contains identity/session state and should be private to the bot user. Recommended permissions:

```bash
chmod 700 data
chmod 600 data/omemo.json
```

If the file is corrupt during testing, stop the bot and move the file aside before creating a fresh OMEMO identity. Be aware that this changes the bot's OMEMO identity.

## PubSub / RTBL fetch loops or repeated pages

Some PubSub services ignore RSM pagination or repeat the same page. BanBot detects suspicious pagination loops and skips stale cleanup for that refresh to avoid deleting valid local RTBL state based on incomplete data.

Check `!rtbl list`, run `!rtbl refresh <service> <node>`, and ask the list provider whether the node is a rolling window or an archive-style list.

## Drone fails because `config.py` is missing

CI environments should create a test config before importing the bot modules. The recommended Drone pipeline uses:

```bash
cp config_sample.py config.py
```

The pytest suite also provides a fallback config module for isolated imports.

## Coverage artifacts appear in git status

Generated test artifacts should be ignored and can be deleted locally:

```bash
rm -rf .coverage htmlcov coverage.xml .pytest_cache .hypothesis mutants .mutmut-cache
find . -type d -name "__pycache__" -prune -exec rm -rf {} +
find . -type f -name "*.pyc" -delete
```
