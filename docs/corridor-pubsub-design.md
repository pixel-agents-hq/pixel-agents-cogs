# Corridor event bus (PubSub): design

> **Status: design only.** Nothing in this doc is implemented yet — no
> `EventBusService`, no `publish`/`subscribe` methods on corridor, no
> pico or floorplan wiring. This doc exists to settle the shape and the
> open questions *before* writing that code, per
> [`docs/architecture.md`](architecture.md)'s pattern of thinking through
> the cross-cog picture on paper first. Implementation lands in a
> follow-up PR stacked on top of this one.

## Motivation

Today, the only cog that turns "something happened" into "something
visible on the office canvas" is floorplan, and the only producer it
knows about is Discord's own gateway: `floorplan/adapters/discord_gateway.py`
listens to presence/activity/message events directly and projects them
into `ServerMessage`s itself (see
[`docs/architecture.md` §3a](architecture.md#3a-presence-mirroring-no-corridor-involvement)).
That works because there has only ever been one producer.

pico is about to become a second one. pico's tool-calling loop
(`docs/architecture.md` §4) already does something worth visualizing —
it decides to respond, then acts. Wiring that straight into floorplan
would mean either:

- floorplan grows pico-specific code (`if message came from pico, do X`),
  which breaks the exact boundary [issue #21](https://github.com/pixel-agents-hq/pixel-agents-cogs/issues/21)
  drew: floorplan owns *everything that consumes*, generically — not
  one integration per producer; or
- pico depends on floorplan directly (`required_cogs: floorplan`), which
  breaks the property [`docs/architecture.md` diagram 1](architecture.md#1-runtime-dependency-graph)
  documents today: **nothing in this repo depends on floorplan**. Adding
  that edge would make floorplan load-bearing for pico, when today it's a
  leaf.

corridor already exists to decouple "a cog needs something cross-cutting"
from "which specific cog provides it" — that's exactly what it does today
for permissions (`require_permission`/`capabilities_satisfy`) and reply
rendering (`send_reply`/`render_reply`), per
[`docs/architecture.md` §2](architecture.md#2-ownership-map-who-does-what).
A pub/sub event bus is the same shape of problem: a producer that
shouldn't need to know who (if anyone) is listening, and a consumer that
shouldn't need to know who (if anyone) produces. corridor is the natural
place for a third chokepoint like this, not a new fourth shared cog.

## Goals

- A publishing cog can emit a typed event without knowing whether anything
  is subscribed.
- A subscribing cog can register interest in an event type without
  knowing whether anything publishes it (yet, or ever).
- corridor stays the only new edge either side gains. **No direct
  `pico -> floorplan` dependency is ever introduced** — the dependency
  graph in `docs/architecture.md` diagram 1 does not change shape, only
  corridor's own responsibilities grow.
- A broken or slow subscriber cannot break or block a publisher. Precedent
  already exists for this in this codebase:
  `floorplan/infrastructure/client_hub.py`'s `ClientHub` isolates
  broadcast failures per socket so one dead connection doesn't stop
  delivery to the others — the bus needs the same isolation, per
  subscriber instead of per socket.

## Non-goals (for this doc, and for the first implementation)

- **No implementation in this PR.** Corridor's actual `EventBusService`,
  floorplan's subscription, and pico's `publish` call are all follow-up
  work.
- **No cross-process or persistent bus.** In-process, single event loop,
  scoped to one running bot — the same scope every other corridor service
  already has. Nothing here needs to survive a restart.
- **No free-form event shape.** A small, explicit catalog of event types
  with typed payloads — mirroring how `ReplyField`/`PermissionGroupDef`
  are explicit domain types today, not arbitrary dicts. See "Domain
  model" below.
- **No guaranteed delivery, ordering, or replay.** If nothing is
  subscribed when an event publishes, it's simply dropped — the same as
  floorplan's WebSocket broadcast today drops messages when no client is
  connected.

## High-level architecture

```mermaid
flowchart LR
    subgraph Publishers["Publishers"]
        pico["pico<br/><small>publishes: gate decided to<br/>respond, tool loop acted</small>"]
        pub_future["(future publisher)"]
    end

    subgraph corridor_bus["corridor — event bus"]
        bus["EventBusService<br/><small>publish(event)<br/>subscribe(event_type, handler)</small>"]
    end

    subgraph Subscribers["Subscribers"]
        floorplan["floorplan<br/><small>renders office canvas bubbles<br/>via pixelagents.contracts.outbound</small>"]
        sub_future["(future subscriber)"]
    end

    pico -->|publish| bus
    pub_future -.->|publish, later| bus
    bus -->|dispatch| floorplan
    bus -.->|dispatch, later| sub_future
```

pico is the reference publisher and floorplan the reference subscriber
for this design, because both already exist and both already have a
reason to use the bus today. Nothing about the bus itself is pico- or
floorplan-specific — `toolbox` or a future cog could publish or subscribe
the same way, which is why the diagram leaves a "future" slot on each
side.

## Where this fits in corridor's existing layering

Every cog in this repo follows the same `domain/` / `application/` /
`infrastructure/` / `adapters/` split
([`docs/AGENTS.md` "Internal layering"](AGENTS.md#internal-layering)).
The bus slots into corridor's existing layers the same way
`PermissionService`/`ReplyService` already do — no new layer, no new
cross-cutting concern:

```mermaid
flowchart TB
    subgraph domain["corridor/domain/"]
        event_model["Event<br/><small>(proposed) frozen dataclass:<br/>type, guild_id, source, payload</small>"]
    end
    subgraph application["corridor/application/"]
        perm["PermissionService<br/><small>existing</small>"]
        reply["ReplyService<br/><small>existing</small>"]
        bus_svc["EventBusService<br/><small>proposed</small>"]
    end
    subgraph adapters["corridor/adapters/cog_base.py"]
        chokepoints["require_permission / capabilities_satisfy<br/>render_reply / send_reply<br/>publish_event / subscribe_event <small>(proposed)</small>"]
    end

    bus_svc --> event_model
    chokepoints --> perm
    chokepoints --> reply
    chokepoints --> bus_svc
```

`CogBase.__init__` would wire one `EventBusService` instance the same way
it already wires `PermissionService`/`ReplyService` today
(`corridor/adapters/cog_base.py`) — one bus per bot process, not per
guild, with guild scoping carried on the `Event` itself (see below).

## Domain model (proposed)

```python
@dataclass(frozen=True, slots=True)
class Event:
    type: str                    # namespaced catalog key, e.g. "pico.responded"
    guild_id: int
    source: str                  # publishing cog's Red Cog name, e.g. "Pico"
    payload: Mapping[str, Any]   # shape defined per event `type`, not free-form
```

`type` is a small, explicit catalog — not a free string any cog can
invent on the spot. The catalog should be namespaced by publisher
(`pico.*` today) but *interpreted generically* by subscribers: floorplan
shouldn't need an `if event.source == "Pico"` branch any more than it
needs Discord-specific branches today. Where possible, an event's
`payload` shape should map directly onto the message vocabulary floorplan
already renders through
(`pixelagents/contracts/outbound.py`'s `AgentToolStartMessage`,
`AgentToolDoneMessage`, `AgentStatusMessage`, ...,
[confirmed in `docs/architecture.md` §3c](architecture.md#3c-serving-the-bundle-pixelagents-built))
so a subscriber's translation is closer to a pass-through than a
redesign. Candidate first two event types:

| `type` | Published by | Roughly maps to |
|---|---|---|
| `pico.responded` | pico, after `ToolLoopService.run` finishes with `stopped_reason == "no_tool_calls"` or a successful `send_reply` tool call | `agentToolStart` → `agentToolDone` bubble, the same shape floorplan already gives a Discord message today (see [floorplan's presence-mapping table](../floorplan/Architecture.md#presence-mapping)) |
| `pico.gate_decision` *(maybe — open question)* | pico, after `GateService.decide` | `agentStatus` (`active`/`waiting`) |

This table is illustrative, not final — the real catalog gets settled in
the implementation PR against pico's actual `GateDecision`/`ToolLoopResult`
shapes.

## Subscription lifecycle

Precedent already exists in this exact file for the *opposite* direction:
`corridor/adapters/cog_base.py`'s `register_dependent`/`unregister_dependent`
lets corridor cascade-unload a cog that depends on corridor, so a
dependent never keeps running against a corridor that's gone away. A
subscription has the reverse failure mode — corridor must not keep a
stale handler closure bound to a subscriber's now-unloaded Cog instance —
so the cleanup direction is reversed too: **the subscriber unsubscribes
itself**, from its own `cog_unload`, rather than corridor tracking and
cascading it.

```mermaid
sequenceDiagram
    participant FP as floorplan
    participant C as corridor

    Note over FP: cog_load
    FP->>C: subscribe_event("pico.responded", handler, owner="Floorplan")
    C-->>FP: registered

    Note over FP: ... bot runs ...
    C->>FP: dispatch("pico.responded", event) on publish

    Note over FP: cog_unload / [p]reload floorplan
    FP->>C: unsubscribe_owner("Floorplan")
    C-->>FP: handler dropped
```

Open question for the implementation PR: whether corridor should also
defensively drop an owner's subscriptions if that owner's Cog disappears
from `bot.cogs` without ever calling `unsubscribe_owner` (a crash during
`cog_unload`, say) — `register_dependent`'s cascade exists precisely
because corridor doesn't trust every dependent to clean up after itself
perfectly. The same distrust probably applies here.

## End-to-end example: pico publishes, floorplan renders it

This is the flow the whole design exists to support — closing the loop
between [`docs/architecture.md` §4](architecture.md#4-runtime-data-flow-picos-gate-then-tool-loop)
(pico's tool loop) and
[§3a](architecture.md#3a-presence-mirroring-no-corridor-involvement)
(floorplan's canvas broadcast), with corridor mediating instead of either
cog knowing about the other:

```mermaid
sequenceDiagram
    participant Pico as pico<br/>(ToolLoopService)
    participant C as corridor<br/>(EventBusService)
    participant FP as floorplan
    participant Hub as floorplan's<br/>ClientHub
    participant B as Browser webview

    Pico->>Pico: tool loop runs, ReplyTool sends via corridor.send_reply
    Pico->>C: publish_event(Event("pico.responded", guild_id, "Pico", payload))
    C->>C: look up subscribers for "pico.responded"
    C->>FP: dispatch(event)  [wrapped: a raising handler is<br/>logged, not propagated to pico]
    FP->>FP: translate event.payload -><br/>AgentToolStartMessage / AgentToolDoneMessage
    FP->>Hub: broadcast(message)
    Hub->>B: push over open socket
```

pico never imports anything from floorplan, and floorplan never imports
anything from pico — both only ever talk to corridor. This is the same
shape `docs/architecture.md` §1 already documents for `required_cogs`:
corridor is the one edge every other cog gets, never each other.

## Delivery semantics — open questions for the implementation PR

- **Sync, awaited dispatch, with per-subscriber error isolation.**
  Recommended default: `publish` awaits each subscriber's handler in turn
  inside a `try`/`except`, logs and continues on failure — mirroring
  `ClientHub`'s per-socket isolation. A subscriber that raises must never
  break the publisher's own turn (pico's tool loop shouldn't fail because
  floorplan's rendering threw). Whether dispatch should instead be
  fire-and-forget (`asyncio.create_task` per handler) is worth revisiting
  once there's a second subscriber and real latency data — starting
  synchronous keeps the first implementation's failure modes easy to
  reason about.
- **Guild scoping.** Every `Event` carries `guild_id`. Whether corridor
  itself filters dispatch (e.g. skip a subscriber if the guild has that
  cog disabled) or whether that stays each subscriber's own
  responsibility (floorplan already gates on its own per-guild `enabled`
  config today) is an open question — leaning towards the latter, so
  corridor doesn't need to know every subscriber's own guild-enablement
  model.
- **Ordering and backpressure.** Explicitly out of scope — event volume
  here is bounded by Discord message/interaction rates on a single guild,
  nowhere near where ordering or backpressure would matter. Revisit only
  if that assumption stops holding.
- **Testing.** Every cog's test suite already installs corridor's shared
  `redbot.core`/`discord` stubs via `corridor.testing.install_stubs()`
  (see `corridor/testing.py`, imported by `pico/conftest.py`,
  `floorplan/tests/conftest.py`, etc.). A subscriber cog's unit tests need
  a way to assert "my handler got called with X" without a real corridor
  instance — likely a small in-memory bus test double alongside the
  existing stub set, following that same established convention rather
  than inventing a new one.

## What the follow-up implementation PR needs to land

- [ ] `corridor/domain/models.py`: `Event` (and any per-event payload
      types the settled catalog needs).
- [ ] `corridor/application/event_bus_service.py`: `EventBusService`
      (`publish`, `subscribe`, `unsubscribe_owner`), unit-tested in
      isolation the way `PermissionService`/`ReplyService` are today.
- [ ] `corridor/adapters/cog_base.py`: `publish_event`/`subscribe_event`
      chokepoint methods, wired the same way `send_reply`/
      `require_permission` are.
- [ ] pico: publish `pico.responded` (and any other settled event types)
      from the tool loop's completion path.
- [ ] floorplan: subscribe at `cog_load`, unsubscribe at `cog_unload`,
      translate `Event.payload` into the existing
      `pixelagents.contracts.outbound` message builders and broadcast via
      the existing `ClientHub`.
- [ ] Update [`docs/architecture.md`](architecture.md) once this lands —
      the dependency graph in §1 doesn't change, but §2's ownership map
      and a new data-flow diagram closing the pico → corridor → floorplan
      loop should replace this doc's sequence diagrams with the real,
      shipped shape.
