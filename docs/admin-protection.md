# Admin and Owner Protection

BanBot protects admins and owners from accidental bans.

## What Is Protected

Admin/owner protection checks:

* Admin-room admins/owners
* Protected-room admins/owners
* Current occupants known in the local occupant cache
* Server-side affiliations where the bot has enough rights to query them

Protection applies to:

* Direct JID bans
* Nick-based bans when the nick maps to a protected occupant
* Domain bans that would affect a protected JID/domain
* RTBL-applied hash/domain matches

## Domain Ban Safety

A domain ban such as:

```text
!ban *.example.org
```

is refused if it would match a protected admin/owner account. This prevents accidental wide bans of operator domains.

Generic TLD-level bans such as `*.com` and `*.org` are also rejected by validation.

## Bot Room Rights

The bot must have admin/owner rights in every protected room. If the bot lacks sufficient rights, BanBot refuses ban application in that room and reports the issue to the admin room.

`!status` includes room-rights state and health warnings.

## Fallback Behavior

If server-side affiliation queries require owner rights and the bot only has admin rights, BanBot can fall back to the live occupant cache for protection checks. This is less complete than owner-level affiliation queries, but still protects visible occupants.

## Recommended Operational Practice

* Make the bot owner/admin in every protected room.
* Keep the admin room as the single source of command authorization.
* Run `!syncadmins` after changing admin-room affiliations.
* Run `!status` after room permission changes.
* Use `!sync` after reconnects or manual room changes.
