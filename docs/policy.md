# Public Policy / Rules

BanBot can publish a small public policy/rules text in protected rooms.

This lets room users call `!rules` or `!policy` to see moderation policy, support links, appeal instructions, or local room rules.

## Admin Commands

```text
!policy show
!policy set <text>
!policy enable
!policy disable
!policy clear/delete/remove
!policy help/usage
```

`!rules` is an alias for the policy command in the admin room.

## Public Commands

In protected rooms, users may call:

```text
!rules
!policy
```

Public policy output is shown only when:

* `ALLOW_USER_COMMANDS_IN_PROTECTED_ROOMS=True`
* policy text exists
* policy output is enabled with `!policy enable`

## Set Policy Text

```text
!policy set Please read the room rules before posting.
```

Policy text supports literal `\n` for line breaks:

```text
!policy set Welcome to {room}!\nUse {prefix}why <nick> to inspect bans.
```

## Placeholders

Supported placeholders:

| Placeholder | Meaning |
| --- | --- |
| `{prefix}` | Current `COMMAND_PREFIX` |
| `{room}` | Current room JID |
| `{room_count}` | Number of protected rooms |
| `{admin_room}` | Admin room JID |
| `{bot_name}` | Bot nickname |

## Enable / Disable

```text
!policy enable
!policy disable
```

Disabling policy output keeps the stored text. This is useful when rules should be temporarily hidden but not deleted.

## Clear / Delete / Remove

```text
!policy clear
!policy delete
!policy remove
```

These delete the stored policy text. The command clears text even if public policy output is currently disabled.

## Show

```text
!policy
!policy show
!rules show
```

In the admin room, show includes whether the public policy is enabled or disabled and displays the rendered text.

## Related Docs

* [Commands](commands.md#public-policy--rules)
* [Configuration](configuration.md#bot-settings)
