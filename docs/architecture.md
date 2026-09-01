# Cross-cog architecture

This doc is the one place that shows how this repo's twelve packages —
[`architect`](../architect), [`cctv`](../cctv), [`corridor`](../corridor),
[`deskutils`](../deskutils), [`floorplan`](../floorplan),
[`painter`](../painter), [`pico`](../pico),
[`pixelagents`](../pixelagents), [`suggestionbox`](../suggestionbox),
[`testbench`](../testbench), [`toolbox`](../toolbox), and the CI-only
[`contracts`](../contracts) — relate to and depend on each other. It does
not replace any package's own `Architecture.md` (linked throughout below);
those cover one package's internal layers in depth. This doc's only job is
the picture across packages: who depends on whom to load, who owns what,
and how a request actually crosses package boundaries at runtime.

If you're new to this repo, read [`docs/AGENTS.md`](AGENTS.md) first for
the one-paragraph purpose of each package, then come back here for the
diagrams.

## 1. Runtime dependency graph

This is the `required_cogs` graph declared in each package's `info.json`,
verified against `develop`. It's the most basic "who needs whom to load"
picture — see [`docs/dependency-loading.md`](dependency-loading.md) for
*how* that loading actually happens (Red itself doesn't enforce
`required_cogs`; every cog hand-rolls it).

```mermaid
flowchart BT
    architect["architect<br/><small>second LLM agent, A2A-reachable<br/>via corridor's shared listener</small>"]
    cctv["cctv<br/><small>the only dashboard-hosting cog<br/>two pages: Discord presence + editor</small>"]
    corridor["corridor<br/><small>permissions + reply style<br/>+ PubSub event bus<br/>+ cross-cog tool registry<br/>+ shared LLM connection<br/>+ shared A2A listener/directory<br/>+ OfficeState store + OfficeStateChanged</small><br/><small>hidden COG</small>"]
    deskutils["deskutils<br/><small>current-time utility command<br/>+ LLM tool registration</small>"]
    floorplan["floorplan<br/><small>Pixel Index browsing + loading a<br/>catalogue layout into the shared office</small>"]
    painter["painter<br/><small>third LLM agent, A2A-reachable<br/>via corridor's shared listener<br/>-- color-only, never structural</small>"]
    pico["pico<br/><small>LLM-backed presence,<br/>sole A2A coordinator</small>"]
    pixelagents["pixelagents<br/><small>vendors + builds the webview<br/>+ Semantic IR + OfficeStateFacade</small>"]
    suggestionbox["suggestionbox<br/><small>MCP feedback server<br/>(report_error/suggest_improvement)</small>"]
    testbench["testbench<br/><small>owner-only: manually publishes<br/>corridor bus events</small>"]
    toolbox["toolbox<br/><small>Node.js/npm on the host<br/>+ LLM tool toggle panel</small>"]

    architect -->|required_cogs| corridor
    cctv -->|required_cogs| corridor
    deskutils -->|required_cogs| corridor
    floorplan -->|required_cogs| corridor
    painter -->|required_cogs| corridor
    pico -->|required_cogs| corridor
    pixelagents -->|required_cogs| corridor
    suggestionbox -->|required_cogs| corridor
    testbench -->|required_cogs| corridor
    toolbox -->|required_cogs| corridor
    floorplan -->|required_cogs| pixelagents
    architect -->|required_cogs| pixelagents
    painter -->|required_cogs| pixelagents
    cctv -->|required_cogs| pixelagents
    architect -.->|"register_agent()<br/>(in-process, via corridor)"| corridor
    painter -.->|"register_agent()<br/>(in-process, via corridor)"| corridor
    pico -.->|"A2A over HTTP, one shared port<br/>(networked, not required_cogs)"| corridor
    painter -.->|"A2A over HTTP<br/>consult_architect (structural reads only)<br/>(networked, not required_cogs)"| corridor
    suggestionbox -.->|"register_mcp_server()<br/>(in-process, via corridor)"| corridor
    corridor -.->|"MCP over HTTP<br/>(networked, not required_cogs)"| suggestionbox
```

Notes, all confirmed against each package's `info.json`:

- **corridor's own `required_cogs` is empty.** It sits at the bottom of the
  graph — every other cog depends on it, it depends on nothing here. It now
  also owns the one LLM connection pico and architect both read (moved
  from pico — see [`docs/architect-design.md`](architect-design.md)'s LLM
  provider migration section) and the one shared A2A listener every
  registered agent is mounted on (moved from architect — see
  [`docs/agent-directory-design.md`](agent-directory-design.md)).
- **floorplan, architect, painter, and cctv are the only cogs with two
  `required_cogs` dependencies** — each declares both `corridor` and
  `pixelagents`. Only `cctv` serves any dashboard/WebSocket surface now
  (docs/cctv-design.md); floorplan, architect, and painter depend on
  `pixelagents` purely to reach `OfficeStateFacade` (the one validated
  choke point for the shared office layout/seats, backed by corridor's
  own `OfficeState` aggregates) and, for architect/painter, the Semantic
  IR codec — see [`docs/cctv-design.md`](cctv-design.md) §2.6 and
  [`docs/painter-design.md`](painter-design.md) part A.
- **`painter -> architect` (A2A) is deliberately not a `required_cogs`
  edge either, mirroring `pico -> corridor` (A2A) below** — a *networked*
  call painter makes to ask architect what tiles/walls/furniture exist
  and where (never color; architect stays "colorblind", see
  [`docs/painter-design.md`](painter-design.md) §4). painter degrades to
  "the `consult_architect` tool errors" if architect is
  unloaded/unreachable, it does not fail to load — and painter's own
  color reads/writes against the shared "editor" aggregate work
  regardless, independent of whether architect *or* `cctv` is loaded at
  all.
- **`pico -> corridor` (A2A) is deliberately not a `required_cogs` edge,
  even though pico already depends on corridor for everything else.**
  It's a *networked* HTTP call to corridor's shared A2A listener, the same
  kind of edge `toolbox -> pixelagents` already is below (there,
  "operational, not coded"; here, "networked, not coded") — pico degrades
  to "the `consult_<agent_key>` tool errors" if the target agent is
  unloaded/unreachable or corridor's A2A listener itself is down, it does
  not fail to load. Architect no longer binds a listener of its own — it
  registers an `AgentExecutor`/`AgentCard` with corridor in-process
  instead, so `architect -> corridor` for that registration is already
  covered by architect's existing `required_cogs: corridor` entry. See
  [`docs/agent-directory-design.md`](agent-directory-design.md) for the
  full design and [`docs/architect-design.md`](architect-design.md)
  section 4 for architect's own A2A executor/tool-loop shape (still
  accurate; only listener *ownership* moved).
- **`corridor -> suggestionbox` (MCP) is deliberately not a `required_cogs`
  edge either, mirroring `pico -> corridor` (A2A) above.** It's a
  *networked* HTTP call to suggestionbox's own MCP listener; corridor
  degrades to "no tools from that server" if suggestionbox is unloaded or
  its listener is down, it does not fail to load. `suggestionbox ->
  corridor` for registering that server is already covered by
  suggestionbox's own `required_cogs: corridor` entry. See
  [`docs/suggestionbox-design.md`](suggestionbox-design.md).
- **No cog depends on floorplan, pico, testbench, toolbox, deskutils,
  suggestionbox, architect, painter, or cctv.** They're leaves: things end
  here, nothing in this repo builds on top of them.
- corridor's `info.json` sets `"type": "COG"` and `"hidden": true`. It is a
  real, loaded Red Cog — not the `SHARED_LIBRARY` type `contracts` uses —
  but it's hidden from end users because its own command surface
  (`[p]corridorsettings`) is the only thing meant to be discovered
  directly; see [`docs/corridor.md`](corridor.md)'s opening section for why
  that distinction matters. Functionally it behaves like shared
  infrastructure every other cog is built on, even though `info.json`
  classifies it the same way as any other cog.
- `toolbox` and `pixelagents` have **no direct dependency on each other** —
  neither lists the other in `required_cogs`, and nothing in
  `pixelagents/infrastructure/webview_build.py`'s missing-tool error
  message (`owner_notification_for`) mentions `[p]toolbox node install` or
  any toolbox command; it only says "Install the missing tool(s), then run
  `[p]pixelagents webview rebuild`." The relationship is operational, not
  coded: `toolbox/README.md` documents that toolbox is "useful alongside
  `pixelagents`, whose webview build needs `node`/`npm` present on the same
  host" — a bot owner runs `[p]toolbox node install` on a fresh host
  *before* `pixelagents`' build pipeline can succeed there, but neither cog
  enforces or checks that ordering in code.

## 2. Ownership map: who does what

The dependency graph above says nothing about what each package actually
*does*. Grouped by responsibility instead of by edges:

```mermaid
flowchart TB
    subgraph shared["Shared infrastructure"]
        corridor["corridor<br/><small>permission tiers (role- and Discord-permission-backed)<br/>+ the single reply-rendering chokepoint<br/>(send_reply / render_reply / send_channel_reply)<br/>+ Discord-vocabulary PubSub event bus<br/>(publish_event / subscribe_event)<br/>+ cross-cog LLM tool registry<br/>(register_tool / list_tools_for)<br/>+ shared LLM connection<br/>(llm_settings / llm_client)<br/>+ shared A2A listener + agent directory<br/>(register_agent / list_agents / watch_agents)<br/>+ MCP client + agent-tool-server registry<br/>(register_mcp_server / list_agent_tools_for)<br/>+ OfficeState store + OfficeStateChanged<br/>(read/set/mutate/watch_office_state)</small>"]
    end

    subgraph host["Host tooling (bot-owner only)"]
        toolbox["toolbox<br/><small>downloads Node.js/npm releases onto<br/>the bot host, puts them on PATH.<br/>Also a Components v2 panel letting the<br/>owner turn any [p]help command into an<br/>LLM tool corridor's registry offers to pico</small>"]
        testbench["testbench<br/><small>publishes any corridor bus event<br/>through a Discord UI, for testing</small>"]
    end

    subgraph build["Build pipeline"]
        pixelagents["pixelagents<br/><small>clones pixel-agents-hq/pixel-agents at a<br/>pinned commit, runs npm/vite, writes<br/>webview_dist/ into its own Red data dir.<br/>Also owns OfficeStateFacade -- the one<br/>validated choke point every consumer<br/>reads/writes office layout/seats through.<br/>No dashboard route or WebSocket<br/>of its own.</small>"]
    end

    subgraph serve["Runtime surfaces"]
        cctv["cctv<br/><small>the only dashboard-hosting cog: one<br/>aiohttp listener, two independent pages<br/>over Red Dashboard -- a live Discord-<br/>presence view (ticket-gated editing)<br/>and an open structural/color editor<br/>view -- both backed by corridor's<br/>OfficeState via pixelagents' facade.</small>"]
        floorplan["floorplan<br/><small>browses the Pixel Index layout catalogue<br/>and loads a chosen layout into the<br/>shared Discord-page office layout. No<br/>dashboard, WebSocket, or presence code<br/>of its own -- cctv owns that surface.</small>"]
        pico["pico<br/><small>watches messages, gate decides<br/>react/ignore, acts only through a<br/>bounded LLM tool-calling loop, publishes<br/>AgentReplied onto corridor's bus, is the<br/>sole A2A coordinator -- delegates to<br/>whichever agents corridor currently lists</small>"]
        architect["architect<br/><small>never Discord-facing; runs its own<br/>bounded tool-calling loop per inbound<br/>A2A message. Registers its AgentCard +<br/>AgentExecutor with corridor's shared A2A<br/>listener instead of binding one of its<br/>own. Edits the shared 'editor' office<br/>aggregate through pixelagents'<br/>OfficeStateFacade -- mutated through a<br/>Semantic IR (LLM tools and [p]architect<br/>office ... both call one<br/>OfficeLayoutService). No dashboard or<br/>WebSocket server of its own.</small>"]
        painter["painter<br/><small>never Discord-facing; a third LLM<br/>agent, same A2A shape as architect.<br/>Recolors the same 'editor' aggregate<br/>architect writes, through the same<br/>facade -- floor tiles, walls, furniture<br/>only, never structure. Reads structural<br/>data from architect over A2A.</small>"]
    end

    subgraph utility["General utilities"]
        deskutils["deskutils<br/><small>[p]deskutils time: current time via<br/>Discord's per-viewer timestamp markup<br/>plus explicit UTC/named-zone formatting.<br/>No config, no bus traffic. Also registers<br/>the same logic as an LLM tool corridor's<br/>registry offers to pico, if loaded.</small>"]
        suggestionbox["suggestionbox<br/><small>runs its own MCP tools server<br/>(report_error/suggest_improvement),<br/>posts to a configured Discord channel.<br/>Registers with corridor's<br/>AgentToolServerRegistry so a registered<br/>A2A agent's own tool loop can call the<br/>same tools, gated per agent.</small>"]
    end

    toolbox -.->|"host prerequisite<br/>(operational, not coded)"| pixelagents
    pixelagents -->|"webview_bundle_status()<br/>furniture_style_manifest()<br/>OfficeStateFacade (both kinds)<br/>via bot.get_cog('PixelAgents')"| cctv
    pixelagents -->|"OfficeStateFacade.set_discord_layout()<br/>via bot.get_cog('PixelAgents')"| floorplan
    pixelagents -->|"OfficeStateFacade (editor)<br/>furniture_style_manifest()<br/>via bot.get_cog('PixelAgents')"| architect
    pixelagents -->|"OfficeStateFacade (editor)<br/>furniture_style_manifest()<br/>via bot.get_cog('PixelAgents')"| painter
    corridor -->|"send_reply / render_reply<br/>require_permission / capabilities_satisfy<br/>publish_event / watch_agents<br/>read/watch_office_state"| cctv
    corridor -->|"send_reply<br/>require_permission / capabilities_satisfy"| floorplan
    corridor -->|"send_reply<br/>publish_event<br/>llm_settings / llm_client<br/>list_agents<br/>list_agent_tools_for"| pico
    corridor -->|"llm_settings / llm_client<br/>register_agent / unregister_agent_owner<br/>list_agent_tools_for (per A2A turn)"| architect
    corridor -->|"llm_settings / llm_client<br/>register_agent / unregister_agent_owner<br/>list_agent_tools_for (per A2A turn)"| painter
    painter -.->|"A2A over HTTP<br/>consult_architect (structural reads only)<br/>(networked, terminates at corridor)"| corridor
    pico -.->|"A2A message/send, one shared port<br/>(networked, terminates at corridor)"| corridor
    corridor --> pixelagents
    corridor -->|"send_reply<br/>register_tool / unregister_tool<br/>register_tool_visibility_filter"| toolbox
    corridor -->|publish_event| testbench
    corridor -->|send_reply| deskutils
    corridor -->|"send_channel_reply<br/>register_mcp_server / unregister_mcp_server<br/>list_agents"| suggestionbox
    corridor -.->|"MCP over HTTP, one dedicated port<br/>(networked, terminates at suggestionbox)"| suggestionbox
```

Every arrow into `corridor` in diagram 1 becomes an arrow *out of*
`corridor` here, because corridor is a provider every dependent calls into
— it's not itself calling into anything downstream. The
`pixelagents -> *` edges are deliberately not a filesystem convention:
they're resolved cross-cog via `bot.get_cog("PixelAgents")` (or
`corridor.dependency_loader.ensure_loaded`). `cctv` reads a small
`WebviewBundleStatus` surface (`dist_path`, `ready`, `detail`,
`built_commit`, `built_base_path`) to serve the built bundle — see
[`pixelagents/Architecture.md`](../pixelagents/Architecture.md#the-webview_bundle_status-cross-cog-surface)
and [`docs/dependency-loading.md`](dependency-loading.md). Every consumer
of the shared office layout — `cctv` (both aggregates), `floorplan`
(writes the "discord" aggregate only), `architect` and `painter` (read/
write the "editor" aggregate only) — goes through the same
`OfficeStateFacade`, never a private Config store of its own; see
[`docs/cctv-design.md`](cctv-design.md) §2.6. `architect` and `painter`
additionally read `furniture_style_manifest()` on the same edge — a
generated `{"styles": [...]}` payload derived from the built furniture
catalog, letting their Semantic IR adapters and LLM tool schemas stay in
sync with whatever `pixel-agents` commit is actually vendored without a
hand-maintained table; see
[`docs/architect-semantic-ir-design.md`](architect-semantic-ir-design.md)
section 6.4.

## 3. Runtime data flow: cctv (the most complex path)

`cctv` is the one package that fans out across Discord, Red Dashboard,
corridor, and pixelagents' build output at once — floorplan, architect,
and painter all mutate the layouts it displays, but none of them talk to
Discord Dashboard, a browser, or a WebSocket directly anymore. The full
design rationale (including the options considered and rejected) lives in
[`docs/cctv-design.md`](cctv-design.md) — this section only shows how the
*other packages* enter that picture.

### 3a. Presence mirroring (via corridor's PubSub bus)

```mermaid
sequenceDiagram
    participant D as Discord Gateway
    participant C1 as cctv<br/>(discord_gateway.py,<br/>publisher)
    participant C as corridor<br/>(EventBusService)
    participant C2 as cctv<br/>(event_subscriptions_discord.py,<br/>subscriber)
    participant WS as cctv's discord-page<br/>WebSocket pipeline
    participant B as Browser webview

    D->>C1: presence / activity / message event
    C1->>C: publish_event(AgentPresenceChanged / AgentReplied)
    C->>C2: dispatch to cctv's own subscriber
    C2->>C2: translate to ServerMessage<br/>(agentCreated / agentStatus / agentToolStart / ...)
    C2->>WS: broadcast to the discord-page ClientHub
    WS->>B: push over open socket
    Note over B: every connected socket starts<br/>as a read-only viewer
```

cctv is both publisher and subscriber here — the bus is the seam between
"listening to Discord" and "rendering to the canvas" inside cctv itself,
which is what lets a second producer plug in without cctv's gateway
listeners or translation code changing at all. pico is that second
producer (§4): it publishes `AgentReplied` directly for its own replies,
and `testbench` can publish any of corridor's event types manually, for
testing. cctv's editor page runs an independent, *ungated* version of
this same subscriber (`event_subscriptions_editor.py`) against its own
roster — a genuine A2A agent (`architect`, `painter`, ...) or cctv's own
bot account, never an arbitrary Discord member. Both subscribers call
corridor's `watch_agents` at `cog_load` — one atomic call that subscribes
*and* returns the current roster — so an agent that registered before
cctv loaded still appears immediately, not just from its next presence
change. See [`docs/corridor-pubsub-design.md`](corridor-pubsub-design.md)
for the full domain model, event types, delivery semantics, and the
subscription-lifecycle/defensive-cleanup details this condensed diagram
leaves out.

### 3b. Editor authorization and live layout delivery (corridor-gated)

```mermaid
sequenceDiagram
    participant B as Browser webview
    participant Dash as Red Dashboard<br/>(login)
    participant CV as cctv
    participant C as corridor

    B->>CV: open /cctv/discord/ws (immediately, no wait)
    B->>Dash: background fetch /third-party/cctv/session
    Dash-->>B: {"ticket": "..."} (8h TTL, only if logged in)
    B->>CV: {"type": "authorize", "ticket": "..."} over the open socket
    CV->>CV: resolve ticket -> Discord member
    CV->>C: capabilities_satisfy(member, "keyholder")
    C-->>CV: bot owner OR guild Administrator OR configured Keyholder role
    alt allowed
        CV-->>B: socket upgraded to editor
        B->>CV: saveLayout / saveAgentSeats
        CV->>C: set_office_layout("discord", ...)
        C-->>CV: OfficeStateChanged (via a watch registered at cog_load)
        CV-->>B: layoutLoaded broadcast to every connected discord-page socket
    else denied
        CV-->>B: stays read-only viewer, edit messages dropped server-side
    end
```

Permission *configuration* (which Discord roles count as Keyholder) lives
entirely in corridor, set via `[p]corridorsettings` — cctv holds no role
IDs of its own. The `/cctv/editor/ws` pipeline runs the identical
bootstrap/save mechanics against the `"editor"` aggregate, with no
authorization step at all — anyone who can reach the page may edit it,
mirroring architect's former dashboard model. In both pipelines, the
`OfficeStateChanged` publish on a successful write is what makes a
mutation from *any* writer — the browser editor, `architect`'s or
`painter`'s own LLM tools, a Pixel Index catalogue load into the discord
aggregate — show up live on every connected socket, without that writer
needing to know or reach cctv directly. See
[corridor's permission model](corridor.md#the-permission-model) and
[`docs/cctv-design.md`](cctv-design.md) §2.2/§2.6 for the full
ticket/socket handshake and the atomic watch-and-snapshot primitive
behind it.

### 3c. Serving the bundle pixelagents built

```mermaid
flowchart LR
    PA["pixel-agents-hq/pixel-agents<br/><small>upstream source, pinned commit</small>"]
    PIX["pixelagents<br/><small>clone + npm/vite build<br/>at cog_load</small>"]
    DIST["webview_dist/<br/><small>pixelagents' own<br/>Red cog_data_path</small>"]
    CV["cctv<br/><small>WebviewAssetProvider<br/>(one shared instance)</small>"]
    DASH["Red Dashboard<br/><small>third-party page router</small>"]
    B["Browser"]

    PA -->|"git clone/fetch @ pinned commit"| PIX
    PIX -->|"vite build --base ./<br/>(asset-URL-relative)"| DIST
    DIST -->|"webview_bundle_status()<br/>polled before every render"| CV
    CV -->|"injects &lt;base href='/third-party/cctv/static/'&gt;<br/>(same static route, both pages)"| DASH
    DASH -->|"/third-party/cctv/discord"| B
    DASH -->|"/third-party/cctv/editor"| B
```

The build is asset-URL-relative on purpose — pixelagents doesn't know in
advance which cog will serve it, so it bakes in no cog-specific route.
Unlike the pre-`cctv` world (floorplan and architect each running an
independent `WebviewAssetProvider` copy against the same build output),
`cctv` is the *only* consumer now, so there is exactly one provider
instance and one shared static route serving both pages' assets. See
[pixelagents' "Building `webview_dist`"](../pixelagents/Architecture.md#building-webview_dist)
and [`docs/cctv-design.md`](cctv-design.md) §2.7.

**The static page above is not the whole story for a *live* office.** The
bundle's own JavaScript computes its WebSocket URL from the page's
hostname alone (`wss://<host>/ws`), not its path — so serving the
identical static bundle under two Dashboard routes on the same host is
not enough to keep their *live* connections independent; both would
otherwise try to connect to the same shared path. cctv runs one aiohttp
listener with two distinct paths (`/cctv/discord/ws`, `/cctv/editor/ws`)
and injects a client-side rewrite shim per page that points the bundle's
connection at the right one — the discord page additionally injects a
ticket-shim *after* the rewrite shim (order is load-bearing: the ticket
shim captures `window.WebSocket` at its own injection time, so it has to
wrap the already-rewriting constructor, not the native one). See
[`docs/cctv-design.md`](cctv-design.md) §2.7 for the full mechanism.

## 4. Runtime data flow: pico's gate-then-tool-loop

Verified directly against `pico/adapters/listener.py`,
`pico/application/gate_service.py`,
`pico/application/tool_loop_service.py`, and `pico/tools/reply_tool.py` —
not just the one-grep summary this doc started from. The actual shape:

```mermaid
flowchart TD
    MSG["on_message"] --> G0{"author is bot?<br/>no guild? guild not<br/>enabled? LLM not<br/>configured?"}
    G0 -->|any true| STOP1(["return, no cost"])
    G0 -->|all false| CTX["build ConversationContext<br/>(trigger + last 10 messages)"]
    CTX --> GATE["GateService.decide"]

    GATE --> G1{"reply-to-bot or<br/>mentions bot?"}
    G1 -->|yes| RESPOND
    G1 -->|no| G2{"message contains<br/>the word 'pico'?"}
    G2 -->|no| IGNORE(["IGNORE, no LLM call"])
    G2 -->|yes| CLASSIFY["one LLM classification call<br/>(does not count against<br/>max_tool_calls)"]
    CLASSIFY --> G3{"answer starts<br/>with 'y'?"}
    G3 -->|no| IGNORE
    G3 -->|yes| RESPOND(["RESPOND"])

    RESPOND --> LOOP["ToolLoopService.run<br/>(bounded by max_tool_calls)"]
    LOOP --> LLM["LLM call with tools=[send_reply],<br/>tool_choice=auto"]
    LLM --> G4{"tool_calls<br/>returned?"}
    G4 -->|none| STOP2(["stop: no_tool_calls<br/>(raw assistant text is<br/>kept in history, never<br/>sent to Discord)"])
    G4 -->|yes, and under budget| EXEC["execute each tool call"]
    EXEC --> REPLY["ReplyTool.handler<br/>-> corridor.send_reply(ctx, ...)<br/>-> corridor.publish_event(AgentReplied(...))"]
    REPLY --> LOOP
    G4 -->|budget exhausted| STOP3(["stop: max_tool_calls"])
```

The bounded-loop guarantee is structural, not a convention: `ToolLoopService`
never calls anything Discord-facing itself — the *only* Discord send in the
whole cog is `ReplyTool.handler`, which is why pico stays compliant with
`contracts/discord_replies/lint_reply_channel.py`'s "always through
corridor" rule without needing any pico-specific exception. `pico` ships
one native tool (`send_reply`), plus whatever any other cog has registered
into corridor's cross-cog tool registry — `deskutils`' `time` today,
plus anything the bot owner has opted in through toolbox's tool-toggle
panel (`[p]toolbox tools`) — `ToolLoopService` itself doesn't distinguish
between any of these; the tool-loop shape supports all of them without
changing this diagram. See
[`docs/corridor-tool-registry-design.md`](corridor-tool-registry-design.md)
for how a cog registers one and how pico adapts it, and
[`docs/toolbox-command-tool-toggle-design.md`](toolbox-command-tool-toggle-design.md)
for how toolbox turns an undecorated command into one at runtime.

`ReplyTool.handler` also publishes `AgentReplied` onto corridor's bus
right after a successful send (§3a) — `cctv`'s own subscriber renders it
the same way it renders a tracked member's own message, with no
pico-specific code anywhere in `cctv`. That `send_reply` call also
creates a real Discord message, which `cctv`'s `on_message` listener
(§3a's publisher half) would otherwise see and publish a *second*
`AgentReplied` for — `on_message` specifically excludes messages from
this bot's own account (never other bots) to avoid that double-publish.
See [`docs/corridor-pubsub-design.md`](corridor-pubsub-design.md) for the
full mapping table and the `AgentStatusChanged` publish pico doesn't make
yet.

## 5. Runtime data flow: pico consults a registered agent over A2A

Verified against `pico/tools/consult_agent_tool.py`,
`pico/infrastructure/architect_client.py`,
`architect/infrastructure/a2a_server.py`, and
`corridor/infrastructure/a2a_server.py` — including a live loopback round
trip through corridor's shared listener in
`pico/tests/test_architect_client.py`, not just a description. Full detail
(including the exact a2a-sdk API surface used, and why an earlier
assumption about it — plain pydantic wire types — turned out wrong) lives
in [`docs/architect-design.md`](architect-design.md) section 4 (architect's
own executor/tool-loop shape, still accurate) and
[`docs/agent-directory-design.md`](agent-directory-design.md) (corridor
owning the listener + directory, which superseded architect's *former*
per-agent listener); this is the condensed cross-cog picture.

```mermaid
sequenceDiagram
    participant U as Discord user
    participant P as pico<br/>(ToolLoopService)
    participant CA as pico<br/>ConsultAgentTool
    participant C as corridor<br/>(shared A2A listener)
    participant A as architect<br/>(registered AgentExecutor)

    U->>P: message gates pico in
    P->>C: list_agents() -- build one consult_AGENT_KEY tool per entry
    P->>P: LLM call, tools include consult_architect
    P->>CA: consult_architect(prompt="...")
    CA->>C: a2a-sdk client: message/send to corridor:PORT/architect/
    C->>C: dispatches by mounted path to architect's executor
    A->>C: llm_client() / llm_settings()<br/>(same connection pico uses)
    A->>A: architect's own bounded tool loop<br/>(placeholder tools + corridor's LLM client)
    A-->>CA: completed Task, final text
    CA->>U: corridor.send_reply(...): "📩 architect replied: ..."
    CA-->>P: tool result (status/answer)
    P->>P: continue its own loop with the result
    P->>U: corridor.send_reply(...) via pico's existing reply_tool
```

Not shown above, but happens right when `CA` receives the tool call
(before `CA->>C`): `ConsultAgentTool` first sends its own "🔧 Asking
**architect**: ..." message, so the outgoing question is visible in the
channel before the A2A round trip even starts, not just the answer
afterward.

Three things worth calling out, all by design:

- **`consult_<agent_key>` tools are rebuilt fresh every turn from
  `corridor.list_agents()`** (`adapters/listener.py`'s `_agent_tools`) —
  with zero agents registered, pico offers zero `consult_*` tools that
  turn, the same "absent provider, fewer tools" degradation
  [`docs/corridor-tool-registry-design.md`](corridor-tool-registry-design.md)
  already documents for corridor-registered tools. There is no longer a
  single hardcoded agent/URL to be "not configured" — see
  [`docs/agent-directory-design.md`](agent-directory-design.md).
- **Corridor's A2A listener is a separate network bind from Discord and
  from Red Dashboard** — it's a machine-to-machine JSON-RPC surface, not a
  browser page; `pico -> corridor` (A2A) never touches corridor's reply
  chokepoint from *architect's* side. It does from pico's side, though —
  see the next point.
- **`ReplyTool` is no longer the only Discord-send in pico.**
  `ConsultAgentTool` also calls `corridor.send_reply` directly, twice per
  invocation (the outgoing question, then the raw answer or a failure) —
  a deliberate transparency feature so a Discord user sees the real,
  unparaphrased A2A exchange, not just whatever pico's own LLM later
  chooses to say about it via `ReplyTool`. `ReplyTool` remains the only
  place the LLM's own composed words reach Discord; `ConsultAgentTool`'s
  announcements are deterministic and independent of that choice. Both
  message shapes always appear together for one consult call: pico's own
  final paraphrase is additive, never a replacement.

## 6. CI-only relationships (not `required_cogs`)

The edges above are all things that matter at runtime, when a bot is
actually running. `contracts/` (repo root) introduces a second, unrelated
kind of edge that only exists in CI: Python **imports**, not Red **cog
dependencies**. `contracts/info.json` sets `"type": "SHARED_LIBRARY"`
specifically so Red's Downloader excludes it from cog discovery — it is
never `[p]load`ed by a running bot.

```mermaid
flowchart LR
    subgraph runtime["Runtime cogs"]
        architect2["architect"]
        cctv2["cctv"]
        corridor2["corridor"]
        deskutils2["deskutils"]
        floorplan2["floorplan"]
        painter2["painter"]
        pico2["pico"]
        pixelagents2["pixelagents"]
        suggestionbox2["suggestionbox"]
        testbench2["testbench"]
        toolbox2["toolbox"]
    end

    subgraph ci["contracts/ (CI-only, SHARED_LIBRARY)"]
        pa_verify["contracts.pixel_agents.verify<br/><small>imports cctv.infrastructure.webview<br/>+ pixelagents.infrastructure.webview_build</small>"]
        idx_lint["contracts.pixel_index.*<br/><small>imports floorplan.contracts.pixel_index<br/>(generates + lints contract.yaml)</small>"]
        reply_lint["contracts.discord_replies.lint_reply_channel<br/><small>AST-scans all eleven cog packages</small>"]
    end

    pa_verify -.->|"import"| cctv2
    pa_verify -.->|"import"| pixelagents2
    idx_lint -.->|"import"| floorplan2
    reply_lint -.->|"AST scan, no import"| architect2
    reply_lint -.->|"AST scan, no import"| cctv2
    reply_lint -.->|"AST scan, no import"| corridor2
    reply_lint -.->|"AST scan, no import"| deskutils2
    reply_lint -.->|"AST scan, no import"| floorplan2
    reply_lint -.->|"AST scan, no import"| painter2
    reply_lint -.->|"AST scan, no import"| pico2
    reply_lint -.->|"AST scan, no import"| pixelagents2
    reply_lint -.->|"AST scan, no import"| suggestionbox2
    reply_lint -.->|"AST scan, no import"| testbench2
    reply_lint -.->|"AST scan, no import"| toolbox2
```

Confirmed one-directional: nothing under `pixelagents/` or `cctv/`
imports from `contracts/` (a `grep` across both trees for
`from contracts` / `import contracts`, outside test fixtures, comes back
empty). The dashed arrows above only run in CI — they never execute inside
a live bot process, and reversing the direction of any of them would be a
real, if strange, code change; today it's structurally impossible.

- **`contracts.pixel_agents.verify`** runs the *actual* production build
  path (`pixelagents.infrastructure.webview_build.ensure_webview_built`)
  against the currently pinned commit, then hands the result to `cctv`'s
  real `WebviewAssetProvider` — a consumer-driven contract test, not a
  mock. Repointed here from `floorplan` once `cctv` became the only
  dashboard-hosting cog (docs/cctv-design.md).
- **`contracts.pixel_index.*`** generates `contract.yaml` from
  `floorplan/contracts/pixel_index.py` + `contracts/pixel_index/endpoints.py`
  on every run (never hand-maintained), then verifies it against a live
  Pixel Index environment.
- **`contracts.discord_replies.lint_reply_channel`** is different in kind
  from the two above: it doesn't import any cog's code as a live module at
  all. It statically indexes every function/method in each of
  `architect`, `cctv`, `corridor`, `deskutils`, `floorplan`, `painter`,
  `pico`, `pixelagents`, `suggestionbox`, `testbench`, `toolbox`
  (`COG_PACKAGES` in the script), AST-walks every Red command handler's
  reachable call graph, and fails the build if a handler reaches a raw
  `ctx.send`/`interaction.response.send_message`/`.followup.send` without
  that call graph ever reaching `corridor.send_reply`/`render_reply` (or,
  for corridor's own commands, `self.send_reply`/`self.render_reply`
  directly — corridor is its own renderer).

See [`docs/contract-testing.md`](contract-testing.md) for the full
methodology behind the two `verify` scripts, and
[`pixelagents/Architecture.md`](../pixelagents/Architecture.md#pixelagents-and-the-repo-root-contracts)
for why `pixelagents/contracts/` (webview outbound-message builders, part
of pixelagents' own runtime) and repo-root `contracts/` (this CI-only
package) are two unrelated things that happen to share a name.

Separately, [`.github/workflows/check-cogs.yml`](../.github/workflows/check-cogs.yml)
load-tests each real cog one at a time (discovered dynamically by
scanning for an `info.json`, not a hardcoded list), checking each loads
cleanly from a clean state rather than being silently dragged in by an
earlier cog's own dependency loading. That's a different
CI check again — a Red-Downloader load smoke test, not a Python import or
an AST scan — and doesn't touch `contracts/` at all.
