# OMEMO Support

BanBot can optionally send OMEMO-encrypted replies for encrypted admin commands.

OMEMO support is optional. The bot works normally without OMEMO dependencies installed.

## Configuration

OMEMO configuration is startup-only. Restart the bot after changing any `OMEMO_*` setting.

```python
OMEMO_ENABLED = False
OMEMO_STORAGE_FILE = "data/omemo.json"
OMEMO_AUTO_ENCRYPT_ADMIN_ROOM = True
OMEMO_PLAINTEXT_FALLBACK = False
OMEMO_RESET_ON_IDENTITY_CHANGE = True
```

## Dependencies

Install normal dependencies first:

```bash
pip install -r requirements.txt
```

Install system libraries required by the OMEMO stack. On Raspbian systems this is typically:

```bash
sudo apt install libsodium-dev libxeddsa-dev
```

Then install optional OMEMO dependencies:

```bash
pip install -r requirements-omemo.txt
```

If optional dependencies are missing while `OMEMO_ENABLED=True`, BanBot starts with OMEMO disabled and logs a warning. Plaintext bot functionality is unaffected.

## Dynamic Reply Behavior

BanBot decides whether to encrypt replies based on the incoming command message:

```text
plaintext command  -> plaintext reply
OMEMO command      -> OMEMO reply
```

This avoids static encrypted/plaintext room lists and keeps normal room behavior natural.

## Admin Commands

```text
!omemo status
!omemo devices
!omemo trust
!omemo reset
!omemo reset confirm
!omemo help
```

### Status

`!omemo status` shows:

* whether OMEMO is enabled in config
* whether optional dependencies are available
* whether the OMEMO plugin is ready
* storage path and permissions
* fallback behavior
* configured identity metadata
* whether stored identity metadata matches current `JID`, `RESOURCE`, and `NICK`

### Devices

`!omemo devices` focuses on current admin-room recipients first. It also shows conservative local storage hints found in the JSON storage.

Storage hints are diagnostic only. They may be stale and are not guaranteed to be an active device list.

### Reset

`!omemo reset confirm` moves the current OMEMO storage and identity metadata to timestamped `.bak-*` files and writes fresh identity metadata for the current bot identity.

Restart the bot afterwards so the OMEMO plugin creates and publishes fresh state.

## MUC Recipients

For encrypted MUC replies, BanBot attempts to encrypt to current occupants with visible real JIDs. Occupants without usable OMEMO devices are skipped and the encrypted send is retried.

If no usable recipients remain, BanBot does not leak the reply as plaintext unless `OMEMO_PLAINTEXT_FALLBACK=True`.

## Admin Room Auto Encryption

`OMEMO_AUTO_ENCRYPT_ADMIN_ROOM=True` allows proactive admin-room messages to be encrypted when possible.

Incoming encrypted commands are always answered encrypted when possible regardless of this setting.

## Plaintext Fallback

`OMEMO_PLAINTEXT_FALLBACK=False` is the safer default.

When encryption is required and fails, BanBot logs the failure and does not send plaintext. Enable fallback only if operational availability is more important than avoiding plaintext leakage.

## Storage and Identity

`OMEMO_STORAGE_FILE` stores identity keys, session state, and trust data. Keep this file private.

BanBot also writes identity metadata next to the storage file, for example:

```text
data/omemo.json
data/omemo.identity.json
```

With `OMEMO_RESET_ON_IDENTITY_CHANGE=True`, BanBot rotates old OMEMO storage to timestamped `.bak-*` files when `JID`, `RESOURCE`, or `NICK` changes.

The old storage is backed up, not deleted.

## Backup Integration

Managed ZIP backups include OMEMO storage when all of these are true:

* OMEMO storage exists
* `DB_BACKUP_INCLUDE_OMEMO=True`
* the file is readable

The archive entry is stored as `omemo.json` and described in `manifest.json`.

Because OMEMO storage contains identity/session material, backup archives should be treated as secrets.

See [Backups and Restore](backups.md).

## Logging

Third-party OMEMO libraries can emit many warnings for broken, empty, or forbidden device bundles in public MUCs. BanBot reduces dependency logger noise during normal INFO-level operation while keeping debug output available when `LOG_LEVEL="DEBUG"`.

## Known Limitations

* Real OMEMO interoperability depends on clients publishing valid device lists and bundles.
* MUC OMEMO works best when occupant real JIDs are visible to the bot.
* Full OMEMO live tests require real test accounts/devices and are opt-in.
