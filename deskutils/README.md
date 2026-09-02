# deskutils

Small stateless utilities for time, text counting, and message quoting.

## Overview

Deskutils provides three independent Discord commands with no shared state
and no persistent Config: a Discord-native time display, a character/word
counter, and a safe quote of a message the invoking member can already
see. Each command works on its own from `[p]deskutils ...`; none depend on
a guild having Pico installed.

All three are also registered as Corridor LLM tools at `cog_load`, so if
[`pico`](../pico) is installed, loaded, and enabled for a guild, a user
can ask conversationally for the time or a text's statistics, or reply to
a message and ask Pico to quote it. `count` and `quote` use bare
`@llm_tool()` decorators: the tool's name comes from the qualified
Discord command, its description from the command's docstring, parameter
descriptions from the parameter names, and availability from the
command's own native checks -- so `quote`'s `@commands.guild_only()`
applies to the LLM tool too, with no separate gating logic to keep in
sync. `time` supplies explicit tool metadata instead (a custom name and
description, a required `employee` permission group, and a
`ToolDescription` for its `timezone` parameter). Each command's handler
returns the same structured result to the LLM that it renders to Discord.
See [`docs/corridor-tool-registry-design.md`](../docs/corridor-tool-registry-design.md).
Registration is inert if pico never loads.

## Commands

| Command | Description |
|---|---|
| `[p]deskutils time [timezone]` | Show the current time: Discord's native timestamp markup (auto-localized per viewer) plus explicit UTC. Pass an IANA `timezone` (e.g. `America/New_York`) to also show it explicitly in that zone. Requires the `employee` permission group. |
| `[p]deskutils count <text>` | Count all characters (including whitespace) and whitespace-delimited words in text. |
| `[p]deskutils quote [message-link]` | Quote the replied-to message, or a same-server message supplied by link, with its author and source link. Server-only (not usable in DMs). |

## Configuration

Deskutils requires [`corridor`](../corridor); it's declared as a
`required_cogs` Downloader hint and loaded on demand at `cog_load` via
`dependency_loader.ensure_corridor_loaded()`.

```text
[p]repo add pixel-agents-cogs https://github.com/pixel-agents-hq/pixel-agents-cogs
[p]cog install pixel-agents-cogs deskutils
[p]load deskutils
```

Deskutils has no persistent settings of its own and nothing else to
configure -- each command reads only the system clock, caller-supplied
text, or a Discord message the caller can already access. `time`'s
`employee` permission requirement is corridor's reserved, non-role-backed
tier: `MemberCapabilities.satisfies` treats it as always satisfied for any
member, so in practice the command is open to everyone in the guild.

## Related docs

- [`docs/corridor.md`](../docs/corridor.md) -- how `required_cogs` and
  corridor's dependency-loading work in general.
- [`docs/corridor-tool-registry-design.md`](../docs/corridor-tool-registry-design.md)
  -- the cross-cog LLM tool registry this cog registers into.
