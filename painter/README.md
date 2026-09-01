# painter

An A2A-only LLM agent that recolors the editor office without changing its
structure.

Painter registers on Corridor's shared A2A listener and uses the same shared LLM
connection as Architect and Pico. Its write surface can recolor tiles, walls,
and furniture, but cannot add, remove, move, or resize anything. It may consult
Architect over A2A for structural context.

Painter and Architect read and write the same revisioned `editor` aggregate
through Pixelagents. Each successful write advances the aggregate revision and
preserves avatar-seat records. CCTV observes the resulting state event and
updates its editor page; Painter has no direct callback to Architect and owns no
browser transport.

## Installing

```text
[p]cog install pixel-agents-cogs painter
[p]load painter
```

Corridor and Pixelagents are required and loaded on demand. Painter can still
read and recolor the editor state when Architect or CCTV is unloaded; only the
optional `consult_architect` tool then becomes unavailable.

## Commands

| Command | Description |
|---|---|
| `[p]painter status` | Show LLM, A2A registration, tool-loop, and editor-revision status |
| `[p]painter maxtoolcalls <count>` | Set the per-turn tool-call limit |
| `[p]painter debuglogging <bool>` | Toggle per-tool-call diagnostics |
| `[p]painter prompt set/reset/show` | Manage the system prompt |

## Tools

| Tool | Responsibility |
|---|---|
| `consult_architect` | Ask Architect for structural context |
| `describe_tile_colors` | Read tile colors in a region |
| `describe_furniture_colors` | Read furniture colors |
| `recolor_tiles` | Change floor or wall color without changing tile kind/material |
| `recolor_furniture` | Recolor one furniture item |
| `recolor_furniture_by_style` | Recolor matching furniture |

See [Architecture.md](Architecture.md) and
[`docs/cctv-design.md`](../docs/cctv-design.md).
