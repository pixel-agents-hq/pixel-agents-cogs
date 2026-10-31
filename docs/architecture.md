# Cross-cog architecture

This document describes ownership and runtime data flow across the repository's
fifteen cogs plus the CI-only `contracts` package. Package-specific architecture
documents remain the source for internal layering.

## Runtime dependencies

`required_cogs` is a Downloader installation hint; each cog also resolves its
runtime dependencies explicitly. Corridor is the shared foundation.

```mermaid
flowchart BT
    Corridor["corridor<br/>shared services + opaque office state"]
    Pixelagents["pixelagents<br/>bundle + schema facade"]
    CCTV["cctv<br/>two browser pages"]
    Floorplan["floorplan<br/>Pixel Index"]
    Architect["architect<br/>structural agent"]
    Painter["painter<br/>color agent"]
    Pico["pico<br/>Discord LLM coordinator"]
    Toolbox["toolbox"]
    Deskutils["deskutils"]
    Suggestionbox["suggestionbox"]
    Telephonepole["telephonepole"]
    Bootcamp["bootcamp"]
    Testbench["testbench"]
    Animator["animator<br/>pixel-art agent"]
    Switchboard["switchboard<br/>requirements + load order"]

    Pixelagents --> Corridor
    CCTV --> Corridor
    CCTV --> Pixelagents
    Floorplan --> Corridor
    Floorplan --> Pixelagents
    Architect --> Corridor
    Architect --> Pixelagents
    Painter --> Corridor
    Painter --> Pixelagents
    Pico --> Corridor
    Toolbox --> Corridor
    Deskutils --> Corridor
    Suggestionbox --> Corridor
    Telephonepole --> Corridor
    Bootcamp --> Corridor
    Testbench --> Corridor
    Animator --> Corridor
```

Switchboard has no edge: it depends on no other cog and declares no
requirements, because it is the cog that installs the other cogs'
requirements and loads them in `required_cogs` order. Corridor cannot be
loaded until that has happened (see
[`dependency-loading.md`](dependency-loading.md#requirements-and-load-order-for-addpath-cogs-switchboard)).

Painter's optional A2A call to Architect and Pico's calls to registered agents
are network interactions through Corridor's shared listener, not dependency
edges. Suggestionbox similarly registers an MCP server with Corridor, and
Telephonepole registers an open-ended, bot-owner-managed set of third-party
ones the same way; a missing server removes those tools rather than
preventing other cogs from loading. Bootcamp registers an open-ended set of
dynamically-created agents with Corridor's agent directory the same way
Architect/Painter each register their one singleton agent -- a removed
Bootcamp agent simply stops appearing in Pico's tool list, the same way an
unloaded Architect/Painter would.

All cogs in this repository are deployed from one synchronized revision.
Mixed-version runtime protocols are unsupported and fail through ordinary import
or API errors; no compatibility negotiation is implemented.

Runtime dependency edges also mean a dependent must react when Corridor or
Pixelagents reload independently of it, after this diagram's `-->` edges are
already resolved — see [`dependency-cascades.md`](dependency-cascades.md) for
Corridor's cascade-unload of dependents versus Pixelagents pushing a fresh
Cog reference to them instead.

## Ownership map

| Owner | Exclusive responsibilities |
|---|---|
| Corridor | Permissions/replies, LLM connection, A2A directory/listener, event bus, tool registries, and opaque revisioned office aggregates (one per guild, plus the editor's) |
| Pixelagents | Pixel Agents bundle build, raw layout schema, Semantic IR/codec, furniture manifest, seat validation, lazy initialization, and typed state facade |
| CCTV | Dashboard picker/office/editor/static/session routes, one office listener, one client pipeline per office, Discord guild projection, registered-agent projection, display settings, and browser authorization |
| Floorplan | Pixel Index API/Web settings, catalogue search/detail UI, and loading a layout into the invoking guild's office |
| Architect | A2A/tool behavior and structural editor-layout mutations |
| Painter | A2A/tool behavior and editor-layout color mutations |
| Pico | Discord LLM gate/tool loop and dynamic A2A coordination |
| Toolbox | Host tooling and command-to-LLM-tool controls |
| Deskutils | Small Discord utilities/LLM tools |
| Suggestionbox | Feedback MCP server and agent-tool visibility controls |
| Telephonepole | Runtime registration of third-party MCP servers and per-server, per-agent visibility controls |
| Bootcamp | Runtime creation of custom LLM agents, each with its own system prompt and permission-group gate |
| Testbench | Owner-only manual Corridor event publication |
| Contracts | CI-only consumer and repository policy checks; not a Red cog |

The important boundary is that browser hosting does not live in Floorplan or
Architect, and persisted office JSON does not live in CCTV or Pixelagents.

## Office-state data flow

Corridor stores independent complete aggregates, one per office:

```text
OfficeRef(kind, guild_id)
OfficeState(kind, layout, seats, revision, guild_id)
```

`kind` is `discord` (one aggregate per guild, in guild-scoped Config) or
`editor` (one aggregate, no guild). Every read and write names its aggregate
with an `OfficeRef`. Pixelagents is the only schema-aware path to
them. Layout and seat writes are field-specific, preserve the other field, and
advance a monotonically increasing aggregate revision. Concurrent writes to the
same field are last-write-wins.

Corridor also stores the office characters (`CharacterRecords`): the pixatar
seed code and character profile each Discord account or genuine agent wears,
shared by every office. Pixelagents deals and validates them, and CCTV's pages
draw the roster from them.

```mermaid
flowchart LR
    PixelIndex["Pixel Index"] --> Floorplan
    Floorplan -->|set a guild's DISCORD layout| Pixelagents
    Architect -->|set EDITOR structure| Pixelagents
    Painter -->|set EDITOR colors| Pixelagents
    CCTV -->|layout/seat browser writes| Pixelagents
    Pixelagents -->|validated state API| Corridor
    Corridor -->|complete OfficeStateChanged| CCTV
    CCTV --> OfficePages["one page per guild office"]
    CCTV --> EditorPage["Editor page"]
```

Each guild's office receives catalogue layouts loaded in that guild and that
guild's roster. The editor office receives Architect/Painter mutations and a
registered-agent roster. They share a schema and default, not state. Loading
one layout never changes another aggregate.

Corridor's `watch_office_state(kind, ...)` subscribes a handler to every
aggregate of that kind; for the editor it also captures the snapshot under the
same lock, and a guild office's subscriber reads its aggregate after
subscribing. It publishes complete post-write snapshots after
releasing that lock, awaits subscribers sequentially, isolates errors, and
cancels any office-state subscriber exceeding five seconds. Persistence remains
successful even if display delivery fails.

## CCTV browser flow

```mermaid
flowchart TB
    Bundle["pixelagents webview_dist"] --> Assets["CCTV static route"]
    Dashboard["Red Dashboard"] --> Picker["/third-party/cctv (server picker)"]
    Picker --> OfficePage["/third-party/cctv/office?guild=&lt;id&gt;"]
    Dashboard --> EditorPage["/third-party/cctv/editor"]
    Listener["CCTV aiohttp listener<br/>127.0.0.1:3210"]
    OfficePage --> OfficeWS["/cctv/office/&lt;id&gt;/ws"] --> Listener
    EditorPage --> EditorWS["/cctv/editor/ws"] --> Listener
    Listener --> OfficePipe["that guild's hub/service/revision"]
    Listener --> EditorPipe["Editor hub/service/revision"]
```

Every page uses one bundle and listener, while each office's client hub,
projection, revision and authorization remain isolated. A guild office page
uses a Dashboard session ticket and requires bot-owner/keyholder access in that
guild for writes. The editor page is intentionally open. On every
`webviewReady`, CCTV reads the current aggregate and serializes bootstrap with
event application.

Without CCTV, Floorplan catalogue operations and Architect/Painter mutations
still work. There is simply no browser surface.

## Agent and event flow

Architect and Painter register agent cards/executors with Corridor, and
Bootcamp registers one such pair per custom agent it creates at runtime. Pico
builds its `consult_<agent>` tools fresh from that directory each turn --
skipping any agent whose `required_permission_group` the triggering member
doesn't satisfy, the gate Bootcamp's own agents use -- and sends A2A requests
to Corridor's single listener. Registered-agent load/unload and activity
events are published on Corridor's bus.

Discord gateway publishers also live in Corridor. CCTV is the single office
subscriber: it routes each event to the office of the guild it belongs to (a
genuine agent's activity to its `context_guild_id`'s office), applies that
guild's display policy, and projects it into Pixel Agents messages. Testbench can publish the same event contracts manually.

## Configuration compatibility

The office refactor deliberately uses fresh Config identities for Corridor's
office state, CCTV, Floorplan, and Architect. There is no migration or fallback
read. Old data is not deleted, but the new code cannot read it. The old
Floorplan/Architect Dashboard and WebSocket paths have no redirect or alias.
Guild offices live in Corridor's guild-scoped Config; no global Discord
aggregate is registered or read, so each guild's office starts from the bundled
default. `/third-party/cctv/discord` redirects to the server picker.

## CI-only edges

The root `contracts` package is a `SHARED_LIBRARY`, never loaded by Red.

- Pixel Index checks generate schemas from Floorplan's runtime models and verify
  live production/staging APIs.
- Pixel Agents checks execute Pixelagents' real clone/build path, serve the
  result through CCTV's `WebviewAssets`, and validate outbound messages against
  upstream AsyncAPI.
- Corridor contract generators verify both agent events and office-state
  contracts are synchronized with the domain model.
- Reply-channel lint AST-scans every real cog for Corridor reply compliance.

See [`contract-testing.md`](contract-testing.md),
[`cctv-design.md`](cctv-design.md), and each package README for operational
details.
