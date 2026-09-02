# architect

An A2A-only LLM agent that owns every structural change to the shared
editor office layout: walls, floor tiles, furniture, zones, and seats.

## Overview

Architect registers an agent card and executor on
[`corridor`](../corridor)'s shared A2A listener. Pico and other A2A clients
delegate a task to it by sending it a prompt; Discord users never converse
with Architect directly, and it has no memory across consultations -- each
delegated prompt is answered on its own. Its bounded tool loop runs on
Corridor's shared LLM connection and mutates the office through the
Semantic IR rather than raw Pixel Agents JSON, so the same validation and
persistence rules apply whether a mutation comes from the LLM tool loop or
from a Discord owner command.

Architect reads and writes the revisioned `editor` aggregate through
[`pixelagents`](../pixelagents). Pixelagents validates every candidate
layout and preserves the aggregate's avatar-seat records while Corridor
persists it and increments its revision.
[`painter`](../painter) is architect's color-only counterpart: it shares
the exact same editor aggregate and A2A registration shape, but can only
recolor tiles and furniture, never add, move, remove, or resize anything.
The Discord office is a separate aggregate, owned by
[`floorplan`](../floorplan), and Architect never touches it.

Architect owns no Dashboard route, webview, WebSocket listener, or
presence projection of its own. [`cctv`](../cctv) serves the editor page
and renders whatever state Architect (or Painter) changes.

## Commands

| Command | Description |
|---|---|
| `[p]architect status` | Show LLM, A2A registration, tool-loop, and editor-revision status |
| `[p]architect maxtoolcalls <n>` | Set the per-turn tool-call limit |
| `[p]architect debuglogging <bool>` | Toggle per-tool-call diagnostics |
| `[p]architect prompt set/reset/show` | Manage the system prompt |
| `[p]architect office describe` | Summarize the editor layout |
| `[p]architect office painttiles ...` | Change tile kind/material/color in a region |
| `[p]architect office place/move/remove ...` | Mutate furniture |
| `[p]architect office createzone/resizezone/removezone ...` | Mutate semantic zones |

The settings and office command groups are bot-owner scoped except for the
read-only status command. There are no Architect-specific listener or
webview commands: Discord commands and LLM tools both call the same
`OfficeLayoutService`, so both surfaces receive identical validation and
persistence behavior.

## Configuration

Architect requires Corridor and Pixelagents; both are declared as
`required_cogs` and loaded on demand at `cog_load`.

```text
[p]cog install pixel-agents-cogs architect
[p]load architect
```

The shared LLM connection and A2A listener live on Corridor, not Architect:

```text
[p]corridor llm endpoint <url>
[p]corridor llm key <key>
[p]corridor llm model <model>
[p]corridor a2a host <host>
[p]corridor a2a port <port>
```

Architect's own Config is limited to its tool-loop behavior: the per-turn
`max_tool_calls` budget, the `system_prompt`, and a `debug_logging` toggle
for verbose per-tool-call tracing.

The editor aggregate itself initializes lazily from Pixelagents' bundled
default layout the first time it's needed. If that default is unavailable,
or the persisted state fails validation, Architect surfaces an explicit
error instead of silently resetting the layout.

Architect also adapts any MCP tools currently enabled for it in Corridor's
agent-tool registry (see [`suggestionbox`](../suggestionbox)), fetched
fresh on every A2A turn so an owner's toggle takes effect on Architect's
very next consultation with no cog reload required.

## Related docs

- [`docs/architect-design.md`](../docs/architect-design.md) -- A2A
  registration, tool loop, mutation tools, and validation/error handling.
- [`docs/architect-semantic-ir-design.md`](../docs/architect-semantic-ir-design.md)
  -- the shared Semantic IR and its Pixel Agents JSON codec.
- [`docs/agent-directory-design.md`](../docs/agent-directory-design.md) --
  how Architect (and every other agent) registers with Corridor's shared
  A2A listener and gets discovered by Pico.
- [`docs/cctv-design.md`](../docs/cctv-design.md) -- office state and
  browser ownership.
