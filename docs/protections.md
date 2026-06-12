# Protections

BanBot includes a protection subsystem for protected XMPP MUC rooms.
Protections are stored in SQLite and can be enabled, disabled, and tuned at runtime from the admin room.

## Commands

| Command | Description |
| --- | --- |
| `!protections list [all|page|last]` | Lists all protections with 🟢 enabled / 🔴 disabled state |
| `!protection enable <name>` | Enables a protection |
| `!protection disable <name>` | Disables a protection |
| `!protections <name> config` / `show` | Shows the current config for one protection |
| `!protections <name> set <key> <value>` | Updates one config value for a protection |
| `!protections <name> reset` | Resets one protection to its built-in defaults |
| `!protections reporters add <jid>` | Adds a trusted reporter JID |
| `!protections reporters remove <jid>` | Removes a trusted reporter JID (`delete`, `del`, and `rm` also work) |
| `!protections reporters list [all|page|last]` | Lists configured trusted reporter JIDs |
| `!report <nick|jid> [reason]` | Trusted reporter command, only useful when `TrustedReporters` is enabled |

Common aliases such as `flood`, `mention`, `wordlist`, `joinwave`, and `reporters` can be used instead of the full protection name.

Use `!protections <name> reset` to return a protection to its built-in defaults, including its default enabled/disabled state.

## Available protections

| Protection | Default | Purpose |
| --- | --- | --- |
| `FloodSpamProtection` | disabled | Detects too many messages from one user in a time window |
| `FirstMessageMediaProtection` | disabled | Reacts when a newly observed joiner sends media as their first message |
| `MentionLimitProtection` | disabled | Reacts to messages mentioning too many current room occupants |
| `WordListNewJoinerProtection` | disabled | Reacts to configured words/phrases from recent joiners |
| `JoinWaveShortCircuitProtection` | disabled | Detects join waves and can set the room members-only and moderated |
| `TrustedReporters` | disabled | Counts reports from configured trusted JIDs and takes an action at a threshold |
| `PolicyChangeNotification` | enabled | Announces ban/unban/protection config policy changes in the admin room |

## Actions

Message protections support these actions:

| Action | Effect |
| --- | --- |
| `notify` | Only notify the admin room |
| `warn` | Notify the admin room and warn in the protected room |
| `kick` | Kick the sender from the room |
| `tempban` | Add a temporary global ban through the existing ban system |
| `ban` | Add a permanent global ban through the existing ban system |

When `redact=True` and `REDACTION_ENABLED=True`, message protections try to retract the triggering message using the existing redaction system.

## Examples

```text
!protection enable flood
!protections flood set max_messages 8
!protections flood set window_seconds 60
!protections flood set action tempban
!protections flood set tempban_seconds 1h
```

```text
!protection enable mention
!protections mention set max_mentions 5
!protections mention set action kick
```

```text
!protection enable wordlist
!protections wordlist set words ["free crypto", "airdrop", "telegram me"]
!protections wordlist set join_grace_seconds 15m
```

```text
!protection enable joinwave
!protections joinwave set max_joins 8
!protections joinwave set window_seconds 60
!protections joinwave set lockdown_seconds 15m
```

```text
!protection enable reporters
!protections reporters add alice@example.org
!protections reporters add bob@example.org
!protections reporters list all
!protections reporters set threshold 2
!protections reporters set action tempban
!protections reporters set tempban_seconds 1d
```

## Notes

`FirstMessageMediaProtection` and `WordListNewJoinerProtection` only act on users whose join was observed by the running bot. This avoids false positives after a bot restart where existing occupants would otherwise look like new users.

`JoinWaveShortCircuitProtection` uses the MUC service's room configuration support. The bot must have sufficient room admin/owner rights, and the server must support changing the relevant MUC config fields.
