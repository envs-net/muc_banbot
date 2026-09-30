"""Policy-neutral command metadata, lookup and usage presentation.

The shared core knows command *names*, aliases and documentation.  It never
chooses which users may execute a command or which handler should be invoked.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import cast


@dataclass(frozen=True, slots=True)
class CommandExample:
    """One documented invocation with an optional explanation."""

    command: str
    description: str = ""


@dataclass(frozen=True, slots=True)
class SubcommandSpec[RoleT]:
    """Structured subcommand help; interpretation of *role* belongs to the bot."""

    name: str
    usage: str
    short: str
    aliases: tuple[str, ...] = ()
    examples: tuple[CommandExample, ...] = ()
    role: RoleT | None = None
    context: str = ""
    section: str = ""


@dataclass(frozen=True, slots=True)
class CommandSpec[RoleT]:
    """Immutable command help metadata, without handlers or permission checks."""

    name: str
    usage: str = ""
    short: str = ""
    aliases: tuple[str, ...] = ()
    examples: tuple[CommandExample, ...] = ()
    subcommands: tuple[SubcommandSpec[RoleT], ...] = ()
    category: str = ""
    context: str = ""
    role: RoleT | None = None


def normalize_command_example(value: object) -> CommandExample:
    """Accept the legacy string, mapping, pair and dataclass example forms."""
    if isinstance(value, CommandExample):
        return value
    if isinstance(value, str):
        return CommandExample(value)
    if isinstance(value, Mapping):
        command = value.get("command", value.get("example", ""))
        return CommandExample(
            str(command or ""),
            str(value.get("description", value.get("short", "")) or ""),
        )
    if isinstance(value, (tuple, list)) and value:
        return CommandExample(str(value[0]), str((value[1] if len(value) > 1 else "") or ""))
    return CommandExample(str(value))


def normalize_command_examples(values: object) -> tuple[CommandExample, ...]:
    """Normalize examples without treating one string or mapping as an iterable."""
    if not values:
        return ()
    if isinstance(values, (str, Mapping, CommandExample)):
        values = (values,)
    return tuple(normalize_command_example(v) for v in cast(Iterable[object], values))


def normalize_subcommand[RoleT](
    value: object,
    *,
    parse_role: Callable[[object], RoleT] | None = None,
) -> SubcommandSpec[RoleT]:
    """Convert structured subcommand metadata without assigning authorization policy."""
    if isinstance(value, SubcommandSpec):
        return value
    if not isinstance(value, Mapping):
        raise TypeError(f"Unsupported subcommand metadata: {value!r}")
    raw_role = value.get("role")
    role = parse_role(raw_role) if raw_role is not None and parse_role else cast("RoleT | None", raw_role)
    alias_values = value.get("aliases", ()) or ()
    if isinstance(alias_values, str):
        alias_values = (alias_values,)
    return SubcommandSpec(
        name=str(value.get("name", "") or ""),
        usage=str(value.get("usage", "") or ""),
        short=str(value.get("short", value.get("description", "")) or ""),
        aliases=tuple(str(alias) for alias in alias_values),
        examples=normalize_command_examples(value.get("examples", ()) or ()),
        role=role,
        context=str(value.get("context", "") or ""),
        section=str(value.get("section", "") or ""),
    )


def command_spec_from[RoleT](
    value: object,
    *,
    parse_role: Callable[[object], RoleT] | None = None,
) -> CommandSpec[RoleT]:
    """Snapshot a command-like object into a stable read-only help contract."""
    subcommands = getattr(value, "subcommands", ()) or ()
    if isinstance(subcommands, (Mapping, SubcommandSpec)):
        subcommands = (subcommands,)
    aliases = getattr(value, "aliases", ()) or ()
    if isinstance(aliases, str):
        aliases = (aliases,)
    role = getattr(value, "role", None)
    return CommandSpec(
        name=str(getattr(value, "name", "") or ""),
        usage=str(getattr(value, "usage", "") or ""),
        short=str(getattr(value, "short", "") or ""),
        aliases=tuple(str(alias) for alias in aliases),
        examples=normalize_command_examples(getattr(value, "examples", ()) or ()),
        subcommands=tuple(normalize_subcommand(v, parse_role=parse_role) for v in subcommands),
        category=str(getattr(value, "category", "") or ""),
        context=str(getattr(value, "context", "") or ""),
        role=parse_role(role) if role is not None and parse_role else cast("RoleT | None", role),
    )


def command_tokens(value: str | Iterable[str]) -> tuple[str, ...]:
    """Normalize command names for lookup while preserving caller argument case."""
    if isinstance(value, str):
        return tuple(value.lower().split())
    return tuple(str(part).lower() for part in value)


def resolve_longest_command[CommandT](
    text: str,
    commands: Mapping[tuple[str, ...], CommandT],
) -> tuple[CommandT | None, list[str]]:
    """Return the longest registered command and its unmodified arguments."""
    parts = text.split()
    normalized = command_tokens(parts)
    for n in range(len(parts), 0, -1):
        candidate = commands.get(normalized[:n])
        if candidate is not None:
            return candidate, parts[n:]
    return None, parts


def is_command_family(prefix: str | Iterable[str], keys: Iterable[tuple[str, ...]]) -> bool:
    """Identify prefixes with descendants, excluding exact-only commands."""
    words = command_tokens(prefix)
    return bool(words) and any(len(key) > len(words) and key[: len(words)] == words for key in keys)


def resolve_help_topic(topic: str | Iterable[str], aliases: Mapping[str, str]) -> str:
    """Normalize a help topic, resolving the longest alias even for nested topics.

    Alias targets are a caller-defined policy: this function only rewrites
    names, never permissions or dispatch routes.
    """
    parts = command_tokens(topic)
    visited: set[tuple[str, ...]] = set()
    while parts and parts not in visited:
        visited.add(parts)
        # Longest aliases first (e.g. ``room invites`` before ``rooms``).
        for count in range(len(parts), 0, -1):
            replacement = aliases.get(" ".join(parts[:count]))
            if replacement is not None:
                parts = (*command_tokens(replacement), *parts[count:])
                break
        else:
            break
    return " ".join(parts)


def format_command_usage(usage: str, prefix: str) -> list[str]:
    """Return legacy-compatible prefix-resolved usage entries."""
    return [usage.format(prefix=prefix)] if usage else []


def format_command_examples(examples: Iterable[CommandExample], prefix: str) -> list[CommandExample]:
    """Format the same documented examples for any client UI."""
    return [
        CommandExample(example.command.format(prefix=prefix), example.description.format(prefix=prefix))
        for example in examples
    ]


def render_subcommand_usage(spec: CommandSpec[object], prefix: str) -> str:
    """Render a short `Usage:` block from structured subcommand metadata."""
    lines = [f"  {entry.usage.format(prefix=prefix)}" for entry in spec.subcommands]
    if spec.usage:
        lines.insert(0, f"  {spec.usage.format(prefix=prefix)}")
    return "Usage:\n" + "\n".join(lines) if lines else ""


def parse_prefixed_command(body: str, prefix: str) -> tuple[str, list[str]] | None:
    """Split a prefixed command, preserving argument case and ordering.

    A missing prefix or a non-command returns ``None``.  The caller retains
    responsibility for filtering the message type and authorizing execution.
    """
    if not prefix or not body.startswith(prefix):
        return None
    parts = body.split()
    if not parts:
        return None
    return parts[0][len(prefix):].lower(), parts[1:]


@dataclass(frozen=True, slots=True)
class CommandHelpDocument:
    """Usage layout for a command; all permission/dispatch policy stays in the bot.

    ``command`` contains structured usage/subcommands/examples. ``notes`` is
    literal explanatory text (e.g. documented ``{room}`` placeholders) and is
    intentionally *not* interpreted as a format template.
    """

    command: CommandSpec[object]
    inline: bool = False
    notes: str = ""
    trailing_newlines: int = 0


def render_command_help_document(document: CommandHelpDocument, prefix: str) -> str:
    """Render structured command usage and examples without changing legacy layout."""
    if document.inline:
        if document.command.subcommands:
            raise ValueError("Inline help documents cannot define subcommands")
        usage = document.command.usage.format(prefix=prefix)
        body = f"Usage: {usage}" if usage else ""
    else:
        body = render_subcommand_usage(document.command, prefix)

    if document.command.examples:
        examples = format_command_examples(document.command.examples, prefix)
        examples_text = "\n".join(
            f"  {example.command}" + (f" - {example.description}" if example.description else "")
            for example in examples
        )
        body += f"\n\nExamples:\n{examples_text}"
    if document.notes:
        body += f"\n\n{document.notes}"
    return body + "\n" * document.trailing_newlines


@dataclass(frozen=True, slots=True)
class CommandHelpSection:
    """Ordered operator-help section, independent of authorization policy."""

    title: str
    entries: tuple[str, ...]


def render_command_help_sections(
    sections: Iterable[CommandHelpSection], prefix: str, *, trailing_newline: bool = False
) -> str:
    """Render sections in order with caller-selected terminal newline behavior."""
    text = "\n\n".join(
        "\n".join((section.title, *(entry.replace("{prefix}", prefix) for entry in section.entries)))
        for section in sections
    )
    return text + "\n" if text and trailing_newline else text
