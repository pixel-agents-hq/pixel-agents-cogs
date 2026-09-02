# CCTV architecture

CCTV is a state observer and browser adapter: it renders and transports
office state, but never owns the schema. Pixelagents validates every write
and provides the office-state facade; Corridor persists the two aggregates
and publishes their change events.

## Overview

| Component | Responsibility |
|---|---|
| `domain/settings.py` | Immutable global and per-guild display-settings values |
| `contracts/websocket.py` | Pydantic-validated parsing of every inbound client message |
| `application/pipeline.py` | Per-page revision ordering, bootstrap, writes, roster, and activity projection |
| `application/tasks.py` | Supervised, cancellable delayed activity clears |
| `infrastructure/settings.py` | CCTV's own Red Config identity for listener/display settings |
| `infrastructure/client_hub.py` | Per-page connection registry, editor flags, and broadcasts |
| `infrastructure/server.py` | One aiohttp listener: two WebSocket routes plus a JSON health route |
| `infrastructure/tickets.py` | Short-lived Discord-page browser identity tickets |
| `infrastructure/webview.py` | Pixelagents bundle loading and per-page HTML/WebSocket-target rewriting |
| `adapters/dashboard.py` | Discord/editor/session/static Dashboard route registrations |
| `adapters/cog_base.py` | Dependency wiring, atomic startup watches, pub/sub projection, lifecycle, health |
| `adapters/commands.py` | Listener, guild, display, delay, and status commands |

```mermaid
flowchart TB
    subgraph Cctv["cctv"]
        direction TB
        Adapters["adapters<br/>dashboard routes, commands, cog_base"]
        Application["application<br/>pipeline, tasks"]
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
    Browser["Browser (Discord / editor page)"]

    Adapters -->|watch_agent_events, capabilities_satisfy| Corridor
    Application -->|office_state, set_office_layout,<br/>mutate_office_seats, watch_office_state| Pixelagents
    Pixelagents -->|persist / atomic watch| Corridor
    Infrastructure -->|webview_bundle_status, static assets| Pixelagents
    Adapters -->|guild + member scan| Discord
    Infrastructure -->|2 WebSocket routes, GET /cctv/health,<br/>Dashboard HTTP| Browser
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
    C->>P: watch_office_state(discord, handler)
    P->>R: atomic watch + snapshot
    R-->>C: current discord OfficeState
    C->>P: watch_office_state(editor, handler)
    P->>R: atomic watch + snapshot
    R-->>C: current editor OfficeState
    C->>R: watch_agent_events(6 Agent* handlers) + list_agents()
    R-->>C: current A2A roster
    C->>D: scan enabled guilds (no await in between)
    C->>C: seed both pipelines, start the listener
```

Each aggregate has exactly one long-lived watcher for CCTV's lifetime, not
one per browser connection. Corridor performs subscribe-and-snapshot under
its office-state lock, so no writer can land in the gap between them.
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

    B->>S: WebSocket connect (/cctv/discord/ws or /cctv/editor/ws)
    S->>S: resolve ticket (discord page only)
    S-->>Pi: register client (user_id, is_editor)
    B->>S: {"type": "webviewReady"}
    S->>Pi: handle_message
    Pi->>PA: office_state(kind)
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
