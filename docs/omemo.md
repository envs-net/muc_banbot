# OMEMO Support

BanBot can optionally send OMEMO-encrypted replies.

## Configuration

OMEMO configuration is startup-only. Restart the bot after changing any `OMEMO_*` setting.

```python
OMEMO_ENABLED = False
OMEMO_STORAGE_FILE = "data/omemo.json"
OMEMO_AUTO_ENCRYPT_ADMIN_ROOM = False
OMEMO_PLAINTEXT_FALLBACK = False
```

## Dependencies

OMEMO support is optional. The base `requirements.txt` installs everything needed for plaintext bot operation, but does not require OMEMO libraries.

Install the system libraries needed by the OMEMO stack first. On Raspbian systems this is typically:

```bash
sudo apt install libsodium-dev libxeddsa-dev
```

Then install the Python OMEMO dependency:

```bash
pip install -r requirements-omemo.txt
```

If these optional dependencies are missing while `OMEMO_ENABLED=True`, BanBot continues to start with OMEMO disabled and logs a warning. Plaintext bot functionality is unaffected.

## Dynamic Reply Behavior

BanBot decides whether to encrypt replies based on the incoming command message:

```text
plaintext command  -> plaintext reply
OMEMO command      -> OMEMO reply
```

This keeps normal room behavior natural and avoids static encrypted/plaintext room lists.

## MUC Recipients

For encrypted MUC replies, BanBot attempts to encrypt to all current occupants with visible real JIDs. Occupants without usable OMEMO devices are skipped and the encrypted send is retried. This avoids failing the whole reply because one occupant has a broken or inaccessible OMEMO bundle.

If no usable recipients remain, BanBot does not leak the reply as plaintext unless `OMEMO_PLAINTEXT_FALLBACK=True`.

## Admin Room Auto Encryption

`OMEMO_AUTO_ENCRYPT_ADMIN_ROOM=True` allows proactive admin-room messages to be encrypted when possible. Without a trigger message, BanBot cannot infer a reply context, so this option controls admin-room proactive behavior explicitly.

## Plaintext Fallback

`OMEMO_PLAINTEXT_FALLBACK=False` is the safer default. When encryption is required but fails, BanBot logs the failure and does not send plaintext.

Enable fallback only if operational availability is more important than avoiding plaintext leakage.

## Storage

`OMEMO_STORAGE_FILE` stores identity keys, session state, and trust data. Keep this file private. A typical path is:

```python
OMEMO_STORAGE_FILE = "data/omemo.json"
```

The bot can create the storage file under its runtime user. The surrounding directory should not be world-readable.

## Logging

Third-party OMEMO libraries can emit many warnings for broken, empty, or forbidden device bundles in public MUCs. BanBot reduces dependency logger noise during normal INFO-level operation while keeping debug output available when `LOG_LEVEL="DEBUG"`.

## Known Limitations

* Real OMEMO interoperability depends on clients publishing valid device lists and bundles.
* MUC OMEMO works best when occupant real JIDs are visible to the bot.
* Full OMEMO live tests require real test accounts/devices and are opt-in.
