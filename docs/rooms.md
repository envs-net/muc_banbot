# Rooms and Invites

BanBot manages a set of protected MUC rooms from the admin room.

Protected rooms are rooms where BanBot enforces local bans, temporary bans, RTBL-applied bans, admin protection, redaction indexing, and public read-only commands when enabled.

## Commands

```text
!room
!room list [all|page]
!room add <room_jid>
!room rejoin <room_jid|all>
!room remove/delete/rm/del <room_jid>
!room invite list [all|page|last]
!room invite accept <id>
!room invite decline/remove/delete/del/rm <id>
!room invite cleanup [expired]
```

`!help room` and `!help room invite` show focused runtime usage.

## Protected Room Lifecycle

### Add a Room

```text
!room add room@conference.example.org
```

BanBot stores the room as protected, joins it, and includes it in future sync/health/ban operations.

The bot account should have admin or owner rights in every protected room.

### Remove a Room

```text
!room remove room@conference.example.org
!room delete room@conference.example.org
!room rm room@conference.example.org
!room del room@conference.example.org
```

Removing a room deletes it from the protected room list and makes the bot leave the room.

### List Rooms

```text
!room list
!room list 2
!room list all
```

Each room line includes the live join state and the bot affiliation, for example:

```text
🟢 room@conference.example.org | joined | bot affiliation: owner
🟢 another@conference.example.org | joined | bot affiliation: admin
🟠 limited@conference.example.org | joined | bot affiliation: member (no admin rights)
🔴 offline@conference.example.org | not joined | bot affiliation: unknown
```

Room list output uses `LIST_PAGE_SIZE` unless `all` is used.

### Rejoin Rooms

```text
!room rejoin room@conference.example.org
!room rejoin all
```

This retries the selected protected-room join without restarting BanBot. The
command reports whether the bot joined and whether it has admin/owner rights.
For successful administrative joins, active bans are synchronized to the room.

Join behavior is controlled by `MUC_JOIN_TIMEOUT_SECONDS` and
`MUC_JOIN_RETRIES`. The periodic health check also retries missing joins
automatically after `HEALTH_CHECK_INTERVAL`.

## Room Invites

Room invites are optional and disabled by default.

```python
ROOM_INVITES_ENABLED = False
ROOM_INVITE_MAX_AGE_DAYS = 30
```

When enabled, incoming MUC invites are announced to the admin room and persisted in the database. BanBot does not auto-join invited rooms. An admin must explicitly accept or decline the invite.

### List Invites

```text
!room invite list
!room invite list last
!room invite list all
```

Expired invites are cleaned up while loading/listing invite state.

### Accept an Invite

```text
!room invite accept 3
```

Accepting an invite attempts to add the room as a protected room. The invite is removed only after the room was added successfully. If room add fails, the invite remains pending so the admin can retry later.

### Decline / Remove an Invite

```text
!room invite decline 3
!room invite remove 3
!room invite delete 3
!room invite rm 3
!room invite del 3
```

All forms remove the pending invite without adding the room.

### Cleanup Invites

```text
!room invite cleanup
!room invite cleanup expired
```

`cleanup` removes all pending invites.

`cleanup expired` removes only invites older than `ROOM_INVITE_MAX_AGE_DAYS`.

Set `ROOM_INVITE_MAX_AGE_DAYS = 0` to keep pending invites indefinitely until accepted, declined, or manually cleaned up.

## Related Sync Commands

```text
!sync
!syncadmins
!syncbans
```

* `!sync` rejoins protected rooms, checks rights, and reapplies active bans.
* `!syncadmins` refreshes admin-room admin/owner state.
* `!syncbans` adopts room outcasts into the database and enforces known active bans.

`SYNC_BATCH_SIZE` controls how many rooms are processed concurrently during sync operations.

## Operational Recommendations

* Keep the bot as admin or owner in every protected room.
* Run `!status` after adding/removing rooms or changing room rights.
* Run `!sync` after server restarts, reconnect loops, or manual room changes.
* Use room invites for controlled onboarding of new protected rooms.
