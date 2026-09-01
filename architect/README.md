# architect

An A2A-only LLM agent that designs the editor office.

Architect registers an agent card and executor on [`corridor`](../corridor)'s
shared A2A listener. Pico and other A2A clients can delegate a task to it;
Discord users do not converse with Architect directly. Its bounded tool loop
uses Corridor's shared LLM connection and can perform structural office
mutations through the Semantic IR.

Architect owns no Dashboard route, webview, WebSocket listener, browser client
hub, or presence projection. [`cctv`](../cctv) owns the editor page and renders
the state Architect changes.

## State ownership

Architect reads and writes the revisioned `editor` aggregate through
[`pixelagents`](../pixelagents). Pixelagents validates the layout and preserves
the aggregate's avatar-seat records while Corridor persists it. Painter uses the
same aggregate for color-only changes. The Discord office is a separate
aggregate and is never changed by Architect.

The editor aggregate initializes lazily from Pixelagents' bundled default. If
that default is unavailable or persisted state is invalid, Architect reports an
explicit error; it does not silently reset the state.

## Installing

Architect requires Corridor and Pixelagents; both are declared as
`required_cogs` and loaded on demand.

```text
[p]cog install pixel-agents-cogs architect
[p]load architect
```

Configure the shared LLM and A2A listener through Corridor:

```text
[p]corridor llm endpoint <url>
[p]corridor llm key <key>
[p]corridor llm model <model>
[p]corridor a2a host <host>
[p]corridor a2a port <port>
```

## Commands

| Command | Description |
|---|---|
| `[p]architect status` | Show LLM, A2A registration, tool-loop, and editor-revision status |
| `[p]architect maxtoolcalls <n>` | Set the per-turn tool-call limit |
| `[p]architect debuglogging <bool>` | Toggle per-tool-call diagnostics |
| `[p]architect prompt set/reset/show` | Manage the system prompt |
| `[p]architect office describe` | Summarize the editor layout |
| `[p]architect office painttiles ...` | Change tile kind/material in a region |
| `[p]architect office describetiles ...` | Describe tiles in a region |
| `[p]architect office place/move/remove ...` | Mutate furniture |
| `[p]architect office createzone/resizezone/removezone ...` | Mutate semantic zones |

The settings and office command surfaces are bot-owner scoped except for the
read-only status command. There are no Architect-specific listener or webview
commands.

## Tools

Architect's office tools describe and mutate the Semantic IR rather than raw
Pixel Agents JSON. The command handlers and LLM tools call the same
`OfficeLayoutService`, so both paths receive the same validation and
persistence behavior. It also adapts any MCP tools enabled for Architect in
Corridor's agent-tool registry.

See
[`docs/architect-semantic-ir-design.md`](../docs/architect-semantic-ir-design.md)
for the IR and [`docs/cctv-design.md`](../docs/cctv-design.md) for the office
state and browser ownership architecture.
