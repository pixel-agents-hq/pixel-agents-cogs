# painter

An A2A-only LLM agent that recolors the shared editor office layout
without changing its structure.

## Overview

Painter registers an agent card and executor on
[`corridor`](../corridor)'s shared A2A listener and runs its own bounded
tool loop on Corridor's shared LLM connection -- the same connection Pico
and Architect use. Other A2A clients (in practice, Pico) delegate a task
to it by sending a prompt; Discord users never converse with Painter
directly, and it has no memory across consultations -- each delegated
prompt is answered on its own.

Painter and [`architect`](../architect) read and write the same
revisioned `editor` aggregate through [`pixelagents`](../pixelagents).
Painter's write surface can recolor floor tiles, wall tiles, and
furniture, but has no `kind`/`material` parameter anywhere, so it cannot
add, remove, move, resize, or otherwise restructure anything -- not
merely by instruction, but because no tool input shape allows it. Painter
may consult Architect over A2A, read-only, for structural context (what
exists and where) that its own tools don't expose.

Each successful recolor loads the current aggregate, applies the color
change, and persists it; Pixelagents preserves avatar-seat records and
Corridor increments the aggregate's revision atomically. Painter has no
Dashboard route, WebSocket listener, or browser client of its own --
[`cctv`](../cctv) serves the editor page and renders whatever state
Painter (or Architect) changes.

## Commands

| Command | Description |
|---|---|
| `[p]painter status` | Show LLM, A2A registration, tool-loop, and editor-revision status |
| `[p]painter maxtoolcalls <count>` | Set the per-turn tool-call limit |
| `[p]painter debuglogging <bool>` | Toggle per-tool-call diagnostics |
| `[p]painter prompt set/reset/show` | Manage the system prompt |

All subcommands are bot-owner scoped except the read-only status command.
There is no Discord-facing recolor command -- every color mutation goes
through Painter's LLM tool loop, reachable only over A2A.

## Configuration

Painter requires Corridor and Pixelagents; both are declared as
`required_cogs` and loaded on demand at `cog_load`.

```text
[p]cog install pixel-agents-cogs painter
[p]load painter
```

The shared LLM connection and A2A listener live on Corridor, not Painter:

```text
[p]corridor llm endpoint <url>
[p]corridor llm key <key>
[p]corridor llm model <model>
[p]corridor a2a host <host>
[p]corridor a2a port <port>
```

Painter's own Config is limited to its tool-loop behavior: the per-turn
`max_tool_calls` budget, the `system_prompt`, and a `debug_logging` toggle
for verbose per-tool-call tracing. It holds no editor state of its own --
the layout lives in Pixelagents' shared store.

Painter can still read and recolor the editor state when Architect or
CCTV is unloaded; only its `consult_architect` tool then errors instead of
answering, since it has nothing to reach.

Painter also adapts any MCP tools currently enabled for it in Corridor's
agent-tool registry (for example Suggestionbox's `report_error`/
`suggest_improvement`, gated per agent via `[p]suggestionbox agents`),
fetched fresh on every A2A turn so an owner's toggle takes effect on
Painter's very next consultation with no cog reload required.

## Related docs

- [`docs/painter-design.md`](../docs/painter-design.md) -- A2A
  registration, tool loop, color mutation tools, and validation/error
  handling.
- [`docs/architect-semantic-ir-design.md`](../docs/architect-semantic-ir-design.md)
  -- the shared Semantic IR and its Pixel Agents JSON codec.
- [`docs/agent-directory-design.md`](../docs/agent-directory-design.md) --
  how Painter (and every other agent) registers with Corridor's shared
  A2A listener and gets discovered by Pico.
- [`docs/cctv-design.md`](../docs/cctv-design.md) -- office state and
  browser ownership.
