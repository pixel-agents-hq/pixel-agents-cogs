# CCTV architecture

CCTV is a state observer and browser adapter: it renders and transports
office state, but never owns the schema. Pixelagents validates every write
and provides the office-state facade; Corridor persists the aggregates (one
office per guild, plus the editor office) and publishes their change events.

## Overview

| Component | Responsibility |
|---|---|
| `domain/settings.py` | Immutable global and per-guild display-settings values |
| `contracts/websocket.py` | Pydantic-validated parsing of every inbound client message |
| `application/pipeline.py` | Per-office revision ordering, bootstrap, writes, roster, and activity projection |
| `application/offices.py` | `GuildOffices`: the open guild offices, one pipeline per enabled guild |
| `application/tasks.py` | Supervised, cancellable delayed activity clears |
| `infrastructure/settings.py` | CCTV's own Red Config identity for listener/display settings |
| `infrastructure/client_hub.py` | Per-office connection registry, editor flags, and broadcasts |
| `infrastructure/server.py` | One aiohttp listener: a WebSocket route per guild office, the editor's, and a JSON health route |
| `infrastructure/tickets.py` | Short-lived guild-office browser identity tickets |
| `infrastructure/webview.py` | Pixelagents bundle loading, per-office HTML/WebSocket-target rewriting, and the server picker page |
| `adapters/dashboard.py` | Picker/office/editor/session/static Dashboard route registrations (and the old `discord` page's redirect) |
| `adapters/cog_base.py` | Dependency wiring, atomic startup watches, pub/sub projection, lifecycle, health |
| `adapters/commands.py` | Listener, guild, display, delay, URL, and status commands |

```mermaid
flowchart TB
    subgraph Cctv["cctv"]
        direction TB
        Adapters["adapters<br/>dashboard routes, commands, cog_base"]
        Application["application<br/>pipeline, offices, tasks"]
        Infrastructure["infrastructure<br/>server, client_hub, tickets, webview, settings"]
        Domain["domain<br/>settings"]
        Adapters --> Application
        Adapters --> Infrastructure
        Application --> Domain
        Application --> Infrastructure
    end

    Corridor["corridor<br/><small>event bus: OfficeStateChanged + Agent* events<br/>keyholder capability check</small>"]
    Pixelagents["pixelagents<br/><small>office-state facade<br/>webview bundle</small>"]
    Discord["Discord gateway cache"]
    Browser["Browser (picker / guild office / editor page)"]

    Adapters -->|watch_agent_events, capabilities_satisfy| Corridor
    Application -->|office_state, set_office_layout,<br/>mutate_office_seats, watch_office_state| Pixelagents
    Pixelagents -->|persist / atomic watch| Corridor
    Infrastructure -->|webview_bundle_status, static assets| Pixelagents
    Adapters -->|guild + member scan| Discord
    Infrastructure -->|office + editor WebSocket routes,<br/>GET /cctv/health, Dashboard HTTP| Browser
```

Corridor and Pixelagents are the only cross-cog dependencies. `cog_base.py`
loads both on demand via `corridor.dependency_loader.ensure_loaded`, never
through a module-scope import, so a reload ordering that runs before either
dependency is loaded cannot crash the module.

## Key flows

### Startup

```mermaid
sequenceDiagram
    participant C as cctv
    participant P as pixelagents
    participant R as corridor
    participant D as Discord cache

    C->>C: load global and guild settings
    C->>P: watch_office_state(discord, dispatcher)
    P->>R: subscribe to every guild office
    C->>P: watch_office_state(editor, handler)
    P->>R: atomic watch + snapshot
    R-->>C: current editor OfficeState
    C->>R: watch_agent_events(6 Agent* handlers) + list_agents()
    R-->>C: current A2A roster
    C->>D: scan enabled guilds (no await in between)
    C->>C: open an office per enabled guild (reads its own state),<br/>seed every office, start the listener
```

Each kind has exactly one long-lived watcher for CCTV's lifetime, not one
per browser connection: the Discord-office watcher dispatches each
`OfficeStateChanged` to the office of its `guild_id`. For the editor,
Corridor performs subscribe-and-snapshot under its office-state lock; for
guild offices CCTV subscribes before any office reads its own state, so a
write landing in between arrives as a newer event. Either way no writer
can land unobserved.
Agent-event subscription and the current A2A roster are captured the same
way, and CCTV's Discord cache scan runs immediately after with no
intervening `await`, so no presence change can be missed at startup.

### Browser connect and live update

```mermaid
sequenceDiagram
    participant B as Browser
    participant S as CctvServer
    participant Pi as CctvPipeline
    participant PA as pixelagents
    participant Co as corridor

    B->>S: WebSocket connect (/cctv/office/<guild id>/ws or /cctv/editor/ws)
    S->>S: route to that guild's office (close 4404 if none is open)
    S->>S: resolve ticket (guild offices only)
    S-->>Pi: register client (user_id, is_editor)
    B->>S: {"type": "webviewReady"}
    S->>Pi: handle_message
    Pi->>PA: office_state(office ref)
    PA->>Co: read current aggregate
    Co-->>Pi: complete OfficeState
    Pi-->>B: assets, settings, existing agents, layout

    Note over Co,Pi: Later -- any writer mutates this aggregate
    Co->>Pi: OfficeStateChanged(state), awaited (5s subscriber timeout)
    Pi->>Pi: ignore if revision <= applied revision
    Pi-->>B: layoutLoaded (only if layout changed) + existing-agents broadcast
```

A pipeline never trusts its own startup snapshot for a late-connecting
client: `webviewReady` always re-reads the current aggregate and
serializes that read against concurrent event application under the
pipeline's lock, so a client that connects mid-write still bootstraps from
a consistent state. `layoutLoaded` is only rebroadcast when the layout
field itself changed -- most revision bumps are seat-only (an agent spawned,
despawned, or was assigned a palette) and would otherwise race the editor's
own unsaved-changes guard.

See [`docs/cctv-design.md`](../docs/cctv-design.md) for the full domain
model, the WebSocket/HTTP route reference, and validation/error-handling
detail.
