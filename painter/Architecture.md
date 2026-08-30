# painter Architecture

painter is a second, A2A-only LLM agent, structurally a near-mirror of
[`architect`](../architect)'s own shape -- see
[`docs/painter-design.md`](../docs/painter-design.md) for the full design
this was built from, including why this is a *parallel copy* of
architect's tool-loop/A2A-server/corridor-LLM-client plumbing rather than
a shared library (the same "duplicated, not shared" convention
`architect/tools/base.py`'s own docstring documents, reaffirmed here --
see `docs/architect-design.md` §8's note that this was deliberately
deferred "until a third agent needs the same shape").

## Internal structure

| File | Responsibility |
|---|---|
| `domain/models.py` | `GlobalSettings` -- painter's own bot-owner-scope tool-loop settings. No `ws_host`/`ws_port`: unlike architect, painter serves no WebSocket transport or webview of its own. |
| `application/tool_loop_service.py` | `ToolLoopService` -- painter's bounded tool-calling loop. Parallel copy of architect's own. |
| `application/painter_layout_service.py` | `PainterLayoutService` -- the color-only mutation surface: `describe_tile_colors`/`describe_furniture_colors` (reads) and `recolor_tiles`/`recolor_furniture`/`recolor_furniture_by_style` (writes). No `kind`/`material` parameter exists anywhere in this module -- painter's write surface is physically incapable of adding, removing, moving, or restructuring anything, not just instructed not to. |
| `infrastructure/settings_repository.py` | `RedPainterRepository` -- Config-backed `GlobalSettings` storage, painter's own identifier. |
| `infrastructure/office_layout_repository.py` | `OfficeLayoutRepository` -- reads/writes the *same* shared office layout architect does, via `pixelagents.infrastructure.pixel_agents_adapter`'s codec and `pixelagents.infrastructure.office_layout_settings.RedOfficeLayoutSettings` (independently constructed here and in architect, resolving to the same on-disk store by identifier + `cog_name`, not a shared Python reference -- see `docs/painter-design.md` part A). |
| `infrastructure/corridor_llm.py` | `CorridorLLMClient` -- lazy proxy to corridor's shared LLM connection. Parallel copy of architect's own. |
| `infrastructure/architect_client.py` | `ArchitectClient` -- A2A client painter uses to consult architect. Parallel copy of pico's own `architect_client.py` (generic across any consulted agent by design). |
| `infrastructure/a2a_server.py` | `PainterAgentExecutor` + `build_agent_card` -- painter's A2A surface, registered with corridor's shared listener at `cog_load`. Parallel copy of architect's own. |
| `tools/base.py` | `ToolSpec` Protocol -- parallel copy of architect's own. |
| `tools/consult_architect_tool.py` | `ConsultArchitectTool` -- painter's one structural-read tool. Resolves architect's current A2A URL from `corridor.list_agents()` fresh on every call (painter only ever consults this one, known agent, unlike pico's one-tool-per-registered-agent shape). |
| `tools/painter_tools.py` | The five color-only LLM tool wrappers around `PainterLayoutService`. |
| `tools/agent_tool_server.py` | Adapts corridor's cross-cog MCP tool registry (suggestionbox) into painter's own `ToolSpec`. Parallel copy of architect's own. |
| `adapters/cog_base.py` | Composition root: wires everything above, registers with corridor at `cog_load`. |
| `adapters/commands.py` | `[p]painter ...` settings commands -- bot-owner scope, no per-guild toggle (painter is process-scoped, not guild-scoped, same as architect). |

## What painter deliberately has none of

No WebSocket server, no webview, no Dashboard route, no presence-tracking
mixin, no Discord conversation loop, no `@llm_tool`-decorated Discord
commands (painter's own tools live in its A2A tool loop, a completely
different registry from corridor's cross-cog Discord LLM tool registry
`toolbox`/`deskutils` use). See `docs/painter-design.md` §0 for why --
two deliberate departures from how the issue that created this cog
originally framed it.

## The shared office layout

architect and painter both read/write one Pixel Agents office layout,
owned by `pixelagents` (`docs/painter-design.md` part A) -- not by
either agent cog. Neither cog needs the other loaded to reach it: each
constructs its own `RedOfficeLayoutSettings`/`OfficeLayoutRepository`
independently, resolving to the same underlying Config document by
identifier + `cog_name` alone. architect owns every structural mutation
(`paint_tiles`, `place_furniture`, `move_furniture`, `remove_furniture`,
zones); painter owns every color mutation. Neither validates the other's
half of the invariant space -- e.g. painter's `recolor_tiles` never
checks whether a cell it's recoloring is occupied by furniture, since
occupancy is architect's concern, not a color concern.
