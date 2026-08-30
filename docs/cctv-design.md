# Extracting dashboard-hosting into a new `cctv` cog: options

**Status: investigation only.** No decision has been made and no code has
changed. This doc lists options for a future refactor that would pull
`WebSocketServer`/`ClientHub`/`TicketStore`/`WebviewAssetProvider`/Red
Dashboard route registration out of `floorplan` and `architect` into a new
cog, `cctv`, such that: loading `cctv` gives one unified dashboard; not
loading it means no dashboard/webview presence at all; and `floorplan`,
`architect`, and (once built, see issue #55) `painter` keep working fully
— presence sync, LLM tool-driven layout mutation — with zero dashboard
code of their own. Every claim below was checked directly against
`develop` on 2026-08-30, not assumed from `docs/architect-design.md` or
`docs/painter-design.md` alone, though both are cited throughout since
they document the two dashboards' history and current shape in depth.

## 1. Current state (verified)

This section is the shared factual basis every option below reasons
from.

### 1.1 Two dashboards, two independent everything

`floorplan` and `architect` each carry a **complete, independent copy**
of the dashboard stack:

| Concern | floorplan | architect |
|---|---|---|
| Layout + seats storage | `floorplan/infrastructure/settings.py` — floorplan's own Config identifier (`8364586608`), `layout`/`seats` keys | Delegates to `pixelagents.infrastructure.office_layout_settings.RedOfficeLayoutSettings` — a *different* Config identifier, shared with `architect`'s own `OfficeLayoutRepository` and (once built) `painter`'s |
| Live WebSocket server | `floorplan/infrastructure/websocket.py` + `client_hub.py`, external path `/ws` | `architect/infrastructure/websocket.py` + `client_hub.py`, external path `/architect/ws` (deliberately distinct — see §1.2) |
| Dashboard route registration | `floorplan/adapters/dashboard.py::DashboardMixin`, route `/third-party/floorplan` | `architect/adapters/dashboard.py::DashboardMixin`, route `/third-party/architect` — a byte-for-byte parallel class, not imported |
| Static asset serving | `floorplan/infrastructure/webview.py::WebviewAssetProvider` | `architect/infrastructure/webview.py::WebviewAssetProvider` — a second, independent instantiation of the same *shape*, but a fully duplicated class, not a shared import |
| Editor auth | `TicketStore` (`floorplan/infrastructure/tickets.py`), 8h tickets, gated on bot-owner/`keyholder` capability | **None.** `architect/adapters/dashboard.py`'s own docstring: "no `/session` ticket endpoint... architect's layout has no editor-authorization concept at all" — any connected client can mutate architect's layout, by design (`docs/architect-design.md` §5.1) |
| Agent roster on the canvas | `PresenceService` fed from real Discord gateway listeners, mirrored via corridor's bus (§1.3) | `PresenceSubscriptionMixin` (`architect/adapters/presence_subscription.py`) fed from corridor's bus only, plus one hand-reconciled entry for architect's own bot account |

`docs/architecture.md` §2/§3c documents *why* this duplication exists:
`architect/infrastructure/webview.py`'s own docstring says architect
importing floorplan's class directly "would force floorplan's own
package onto disk for anyone installing architect alone," since Red's
Downloader only guarantees a cog's own `required_cogs` are installed
alongside it — `floorplan` was never one of architect's dependencies.
The floorplan/architect split was never designed as "shared library +
two consumers"; it was designed as "two consumers, deliberately
independent," and this shows up as real code duplication today (the
`WebviewAssetProvider` pair being the clearest example — same
`WEBVIEW_CACHE_CONTROL`, same `FURNITURE_KEYS`, same `resolve()`/
`content_type()`/`dashboard_static_response()` bodies, character for
character in most methods).

### 1.2 The one genuinely shared artifact, and why "just share the class" already failed once

`pixelagents/infrastructure/webview_build.py` clones+builds the upstream
`pixel-agents` webview at a pinned commit into pixelagents' own
`cog_data_path`, producing `webview_dist/` (compiled JS/CSS/assets) and a
bundled `default-layout-1.json`. This *is* shared — both floorplan and
architect read it via the identical cross-cog surface,
`pixelagents.webview_bundle_status()` (`bot.get_cog("PixelAgents")`,
`docs/architecture.md` §2) — but it's a **read-only, static build
artifact**, not a live service. Nothing about `cctv` hosting a dashboard
changes this axis; `pixelagents` keeps building it regardless of whether
`cctv` is loaded.

The real cautionary precedent for "can floorplan and architect share
live infrastructure" is `docs/architect-design.md` §5/§9's own incident:
the vendored bundle computes its live WebSocket URL from
`window.location.host` alone (`wss://<host>/ws`), **not the page path**.
Serving the identical static page under two different Dashboard routes
was not enough to keep the two live dashboards independent — both
browser tabs silently connected to whichever cog answered the one
shared `/ws` path (floorplan's). The fix (still in place today) was
architect binding its own WebSocket server on a distinct external path
(`/architect/ws`) plus a client-side `WS_REWRITE_SHIM` patching
`window.WebSocket` before the real connection opens. **Any option below
that unifies the two dashboards under one `cctv`-owned WebSocket server
does not need this workaround at all** — a single server naturally
answers a single `/ws` path for both layouts' clients, since which
layout renders becomes a page-routing question, not a
transport-collision one. But an option that keeps the servers separate
inherits this exact hazard and must keep the rewrite-shim mechanism (or
equivalent) working.

### 1.3 The layout stores are already, unrecoverably, two different things

This is the fact most likely to be missed by anyone assuming "one
dashboard cog" implies "one layout." It doesn't, today:

- **floorplan's layout** is live, presence-mirrored, guild-scoped in
  spirit (though the Config key itself is global — `docs/architecture.md`
  and floorplan's own settings module), editable by any bot-owner/
  keyholder through the in-browser editor, and is the thing the Pixel
  Index catalogue (`[p]floorplan layout load`) reads/writes.
- **architect's (and, once built, painter's) layout** is a *different*,
  independent Config blob (`pixelagents.infrastructure.office_layout_settings`),
  seeded once from pixelagents' bundled default
  (`CogBase._ensure_layout_seeded()`), mutated only through architect's
  Semantic IR tools (`paint_tiles`, `place_furniture`, ...) and painter's
  color-only tools, with no editor-authorization concept at all.

**Verified consequence:** a wall architect adds via `paint_tiles` never
appears in floorplan's live view, and a layout edit made through
floorplan's in-browser editor is invisible to architect's LLM tools —
they are reading and writing two different Config identifiers with two
different seed histories. This is not a bug introduced by dashboard
duplication; it's a deliberate, documented design choice
(`docs/painter-design.md` §2's note: architect's/painter's shared office
is "a distinct, independent, global (not per-guild) layout with no
presence-mirroring concerns," explicitly not the same thing floorplan's
office is). **Any cctv design must decide, explicitly, whether unifying
dashboard *hosting* also means unifying the *data* it renders** — see
Axis 3 below; this is a major fork with real product consequences, not
a footnote.

### 1.4 Presence roster reconstruction today has a real, confirmed cold-start gap

Checked directly (not inferred): `architect/adapters/presence_subscription.py`'s
`_start_presence_tracking()` — called once from `cog_load()` — does
exactly two things: subscribe to corridor's `AgentPresenceChanged`/
`AgentReplied` events, and reconcile architect's own bot account by hand.
**It never calls `corridor.list_agents()`** to seed the roster with
agents that registered *before* architect's own `cog_load` ran. Corridor's
`AgentPresenceChanged` publish happens once, synchronously, at the moment
each agent's `register_agent()` call lands (`corridor/adapters/cog_base.py`,
`docs/corridor-pubsub-design.md`) — there is no replay buffer. So:
reload architect alone while painter is already registered and running,
and architect's own dashboard's agent roster silently omits painter
until painter itself reloads (or corridor is reloaded, unregistering and
re-registering everyone). This is a real, present-day gap in the
existing two-dashboard design, not a hypothetical cctv would introduce —
it directly answers "is the bus alone sufficient for a subscriber to
reconstruct full current state on its own `cog_load`": **no, not for the
agent-presence half**, at least not the way the two existing dashboards
already do it.

The *layout* half is not subject to this gap: both dashboards' bootstrap
sequences (`_send_bootstrap`/`webviewReady` handling,
`floorplan/adapters/office_gateway.py:142-150`,
`architect/adapters/office_gateway.py:50-56`, both calling
`OfficeService.bootstrap_messages(...)`) build a fresh snapshot by
**reading the persisted Config store directly** at handshake time, not
from anything the bus ever carried — the bus only ever carried
"something happened" agent-activity events (`docs/corridor-pubsub-design.md`'s
own "Discord-vocabulary… never the exact webview message" scoping), never
a layout diff or snapshot. So layout state was never bus-dependent to
begin with, and floorplan's own live Discord-member roster is
independently reconstructible too: `floorplan/adapters/discord_gateway.py:45`
builds `snapshots = tuple(self._member_snapshot(member) for member in
guild.members)` — a direct Discord API pull, not a bus replay — for its
own bootstrap. **The gap is specifically "an A2A-registered genuine
agent's presence, as currently implemented by architect's subscriber,"**
not "presence in general" or "layout." See Axis 2 for what this means for
cctv's own cold start.

### 1.5 Write-tools have zero hard dependency on dashboard/broadcast succeeding — confirmed

Checked directly in `architect/application/office_layout_service.py:463-466`:

```python
async def _persist(self, office: Office, styles: FurnitureStyleManifest) -> None:
    raw = await self._repository.save(office, styles)
    if self._broadcast is not None:
        await self._broadcast(raw)
```

`self._repository.save(...)` (the real Config write) happens
unconditionally, first. `self._broadcast` (wired to
`CogBase._broadcast_layout`, `architect/adapters/cog_base.py:192-198`) is
a best-effort push to `ClientHub` (`self._send` → `self._client_hub.broadcast(...)`),
called only *after* the persist already succeeded, with no `try/except`
around it visible at this call site — but `ClientHub.broadcast` itself
does per-socket error isolation (`corridor-pubsub-design.md`'s own
comparison: "mirroring `ClientHub`'s per-socket isolation" — one bad
socket never breaks the broadcast to the others, and there is no
network listener to fail against if zero sockets are connected, which is
exactly the "no dashboard loaded" case). `painter`'s equivalent
(`painter/application/painter_layout_service.py:197-200`) is the same
shape: persist first, then an optional `on_layout_changed` callback
(wired through `bot.get_cog("Architect")`, `painter/adapters/cog_base.py:170-184`,
already a *best-effort, may-be-`None`* cross-cog lookup). **Conclusion:
paint_tiles/recolor_tiles/place_furniture/etc. already tolerate "no
dashboard, no connected client, no architect cog even loaded" today** —
this invariant does not need to be built for cctv, it needs to be
*preserved* by whichever option is chosen (see Axis 4).

### 1.6 The `WebviewAssetProvider` pair is the actual reuse opportunity, not the `WebSocketServer` pair

Worth separating explicitly, since "share the dashboard code" sounds
like one problem but is really two very differently-shaped ones:

- `WebviewAssetProvider` (`resolve`/`content_type`/`dashboard_webview_response`/
  `dashboard_static_response`/`load_assets`/`default_layout`) is **pure,
  stateless-per-request, framework-agnostic** file-serving logic over one
  immutable root directory — it has no notion of "whose layout" at all
  until `base_href`/the injected shim strings are set. This is the
  cleanest candidate for consolidation under any option.
- `WebSocketServer`/`ClientHub` are **stateful, per-cog-instance, live
  connection managers** married to one specific `OfficeService` instance
  and one specific persisted layout. Consolidating these means deciding
  *which* `OfficeService`/layout a unified server multiplexes across
  (Axis 3), not just deduplicating code.

## 2. Options

### Axis 1 — where the dashboard-hosting *code* lives after the refactor

**Option 1A: Fully inside `cctv`, `floorplan`/`architect`/`painter` keep nothing.**

`cctv` owns `WebSocketServer`, `ClientHub`, `TicketStore`,
`WebviewAssetProvider`, and all Dashboard route registration
(`on_dashboard_cog_add`, `dashboard_page`-decorated methods). `floorplan`
loses `floorplan/infrastructure/{websocket,client_hub,tickets,webview}.py`
and `floorplan/adapters/dashboard.py` outright; `architect` loses the
equivalent five files. `cctv` depends on `pixelagents` (for
`webview_bundle_status()`/`furniture_style_manifest()`) exactly as
floorplan/architect already do, and gains a way to reach whichever
domain cogs are loaded (Axis 2) to get presence/layout state to render.

- *Pro:* Cleanest separation — "no cctv, no dashboard" becomes literally
  true because there is no dashboard-hosting code left anywhere else to
  half-run. Matches the goal statement exactly.
- *Pro:* Eliminates the `WebviewAssetProvider` duplication (§1.6) for
  real — one class, one place.
- *Con:* Every domain cog must now expose *something* cctv can pull
  state from (Axis 2) — this option has no in-between; it forces that
  decision immediately rather than letting it be deferred.
- *Con:* The floorplan-editor-vs-architect-no-editor asymmetry (§1.1's
  auth row) becomes cctv's problem to model per-layout, not each
  domain cog's own concern to enforce close to its data.

**Option 1B: A thin shared library (in `pixelagents`, or a new
`SHARED_LIBRARY`-typed package like `contracts`) that `cctv` *and* the
domain cogs still depend on, each still running their own instance.**

Only `WebviewAssetProvider` (and maybe `TicketStore`, which has zero
cog-specific state — see `floorplan/infrastructure/tickets.py`) actually
move to the shared location; each of `floorplan`/`architect`/`painter`
keeps constructing and owning its own `WebSocketServer`/`ClientHub`/
Dashboard routes exactly as today, just importing the deduplicated
pieces instead of hand-copying them. `cctv` in this option isn't "the
one dashboard cog" at all — there would still be three (or however
many) separate `/third-party/*` routes; `cctv` would just be a fourth,
new, *unified* page that happens to reuse the same library.

- *Pro:* Lowest-risk, incremental — doesn't touch the WebSocket/broadcast
  lifecycle at all, so §1.5's already-verified invariant needs zero new
  reasoning.
- *Con:* **This does not satisfy the stated goal.** The goal is "when
  cctv is loaded, a single unified dashboard is available; when not
  loaded, there is no dashboard at all." Option 1B leaves floorplan's and
  architect's own dashboards running independently of whether cctv is
  loaded — cctv becomes an *additional* dashboard, not *the* dashboard.
  Listed here mainly to be explicit about why it's rejected by the goal
  as stated, and as the fallback if a full extraction turns out
  infeasible for reasons found during implementation.
- *Con:* Doesn't remove the `WebSocketServer` cost of running three live
  socket servers/three sets of connected-client state simultaneously
  memory/connection-wise, since each domain cog still runs its own.

**Option 1C: Hybrid — thin shared library for the stateless pieces
(`WebviewAssetProvider`, ticket minting), but the live `WebSocketServer`/
`ClientHub`/Dashboard-route layer moves fully into `cctv`, same as 1A.**

This is really "1A, but `WebviewAssetProvider` itself is factored as an
importable shared class rather than hand-rewritten inside `cctv`" — the
distinction from 1A is purely about *where the file lives*
(`pixelagents/infrastructure/webview.py` vs. `cctv/infrastructure/webview.py`),
not about runtime behavior; 1A could just as well end up here once
implemented, if whoever builds it decides the class belongs in
`pixelagents` (which already owns the build output it serves) rather
than duplicated a third time inside `cctv`.

- *Pro:* Satisfies the stated goal (single dashboard, gone entirely
  without cctv) while still fixing the concrete duplication in §1.6.
- *Con:* Slightly muddies `pixelagents`' own charter — it would now own
  not just "build the bundle" but "know how to serve it as a Dashboard
  page," which today is deliberately *not* pixelagents' job (`docs/architecture.md`
  §2: "No dashboard route, no WebSocket, no Discord presence surface of
  its own" is stated as a `pixelagents` property). This is the same kind
  of charter-expansion tension `docs/painter-design.md` §2 already
  flagged and consciously accepted once for the layout Config move —
  worth a similar explicit sign-off rather than assuming it's fine by
  precedent.

**Recommendation shape (not a decision):** 1A/1C are the two live
options; 1B doesn't meet the stated goal and exists here mainly to
document why it was considered and set aside.

### Axis 2 — how `cctv` learns state

This is the axis §1.4's cold-start finding bears on most directly.

**Option 2A: Direct in-process access via `bot.get_cog(...)`.**

`cctv` looks up `bot.get_cog("Floorplan")`/`bot.get_cog("Architect")`/
`bot.get_cog("Painter")` (whichever are loaded) and calls narrow,
purpose-built methods each domain cog exposes — the same
`dependency_loader.ensure_loaded`/`bot.get_cog` pattern already used for
`pixelagents.webview_bundle_status()` (`docs/architecture.md` §2) and for
painter's `notify_shared_layout_changed()` cross-cog hook
(`docs/painter-design.md` §8, `painter/adapters/cog_base.py:170-184`).

- *Pro:* No cold-start gap at all — `cctv`'s own `cog_load()` can pull a
  full, current snapshot synchronously the moment it loads, regardless
  of load order relative to the domain cogs. This directly closes §1.4's
  gap rather than inheriting it.
- *Pro:* Precedented pattern already used for exactly this kind of
  cross-cog reach in this repo (pixelagents↔floorplan/architect,
  painter↔architect).
- *Con:* Couples `cctv` to each domain cog's Python surface (even if only
  a narrow Protocol-shaped method), meaning a domain cog *can't* be
  developed or reloaded fully independently of cctv's expectations of it
  — every existing precedent for this pattern in the repo is already
  "one narrow method, best-effort, `None`-checked," so this is a
  continuation of an established shape, not a new kind of coupling.
- *Con:* `cctv` needs one such surface per domain cog (floorplan's
  members+activities, architect's/painter's shared layout + genuine
  agents) — not a single uniform interface, since floorplan's presence
  model (real Discord members) and architect's (genuine A2A agents) are
  different shapes today (`GenuineAgentKey` vs. Discord snowflakes,
  `docs/office-agent-identity-design.md`).

**Option 2B: Corridor pubsub only, accepting the cold-start gap.**

`cctv` subscribes to all six `AgentActivityEvent` types at `cog_load`
(`corridor.subscribe_event`, exactly `floorplan/adapters/event_subscriptions.py`'s
shape) and renders whatever arrives from that point forward. No
in-process cog lookups.

- *Pro:* Cleanest dependency shape — `cctv` only depends on `corridor`
  (for the bus) and `pixelagents` (for the build artifact), genuinely
  zero coupling to floorplan/architect/painter's own Python surfaces.
  This is the "decoupled producer/consumer" ideal the bus was built for
  (`docs/corridor-pubsub-design.md`'s own motivation section).
- *Con:* **Inherits §1.4's gap, and makes it worse.** Today the gap only
  affects architect's own dashboard missing agents that registered
  before architect's `cog_load`. Under this option, `cctv` starts with
  a **completely empty** roster and a **completely empty** layout view
  after every `[p]reload cctv` — not just missing one late registrant,
  missing *everything* — until either the corresponding cogs are
  reloaded (re-publishing their presence) or some other bootstrap
  mechanism runs. Given `cctv` is meant to be the operator-facing "the
  dashboard cog" — plausibly reloaded far more often than
  floorplan/architect/painter individually — this is a real, likely
  user-visible regression versus what floorplan/architect each already
  do for their own bootstrap (§1.4: both already read layout fresh from
  Config directly, not from the bus).
- *Con:* Layout state was **never** carried on the bus at all (§1.4) — this
  option would need a *new* mechanism regardless (a new event type, or a
  pull), so "pubsub only" isn't actually achievable for the layout half
  without Option 2C's addition anyway. This option is only really viable
  for the *presence* half, and even there with the empty-cold-start
  caveat above.

**Option 2C: Pubsub for live updates, plus each domain cog also exposes
a pull/query method for cctv to call once at its own `cog_load` (and
whenever it needs a resync, e.g. after `on_cog_add` for a domain cog
that loads *after* cctv already did).**

This is genuinely the union of 2A and 2B, not a third independent shape:
subscribe to the bus for the steady-state "something changed" stream
(cheap, already-built, matches how floorplan/architect already consume
it), and additionally call a narrow pull method on each domain cog *at
minimum once at cctv's own `cog_load`* to seed the roster/layout, mirroring
what corridor's own `list_agents()` already does for the A2A directory
(`corridor/adapters/cog_base.py`'s `list_agents()` — itself already a
pull-style "current state" query, not something reconstructed from bus
history) and what floorplan's own `guild.members` iteration already does
for Discord presence (§1.4) — extending that same "pull once, then
subscribe for deltas" shape to genuine-agent presence and to layout,
neither of which corridor's *event bus specifically* was ever designed to
carry as a queryable-on-demand snapshot.

- *Pro:* Closes §1.4's gap in general (not just for architect's specific
  case), for both cctv and, as a side effect, would fix architect's own
  existing gap if architect's presence-subscription code were updated to
  the same shape.
- *Pro:* No new corridor bus event types needed — `corridor.list_agents()`
  already exists and already gives the genuine-agent-registration half of
  this pull for free; only the "current layout" pull and "current
  Discord-guild-member presence" pull are net-new surfaces domain cogs
  would need to add (floorplan already has the Discord-member half
  in-process via `guild.members`, so this is really just "expose what it
  already computes for its own bootstrap, to cctv too").
- *Con:* Most implementation work of the three options — it's 2A's
  per-domain-cog surface *and* 2B's subscription wiring, not a shortcut
  past either.
- *Con:* Still needs a resync trigger for "domain cog loads after cctv
  already did" (Red's `on_cog_add`-equivalent, or cctv re-pulling
  periodically, or the domain cog announcing itself the way A2A agent
  registration already does via `AgentPresenceChanged`) — a real design
  detail the investigation didn't fully resolve and a future design pass
  would need to pick concretely.

**Recommendation shape:** 2B alone is the weakest fit given the layout
half is bus-incompatible by construction and the presence half's
cold-start regression is a real, verifiable step backward from what
floorplan/architect already guarantee themselves today. 2A and 2C are
both viable; 2C is closer to "the bus's original intent, extended
correctly" but costs more to build than 2A's "just ask directly, no bus
involvement for this at all."

### Axis 3 — do the two layout stores get unified?

This is the fork flagged in the task and in §1.3 — real product
consequences either way, not a mechanical implementation detail.

**Option 3-separate: Layouts stay separate; `cctv` renders whichever
store it's told, per page/route, unchanged.**

`cctv` would serve (at minimum) two distinct dashboard views — one over
floorplan's Config-backed layout+seats, one over the
pixelagents-owned/architect-and-painter-shared layout — the same two
views that exist today, just hosted from one cog instead of two. No
change to `floorplan/infrastructure/settings.py`'s Config identifier,
no change to `pixelagents.infrastructure.office_layout_settings`, no
data migration.

- *Pro:* Zero data-model risk — this is purely a hosting-layer
  refactor, exactly matching "cctv only unifies dashboard *hosting*"
  from the task's own framing. Confirming this is in scope means the
  refactor's blast radius stays contained to the four listed
  files/classes per cog (§1.1's table), nothing in `pixelagents/domain/office_ir.py`
  or either settings repository needs to move or change shape.
  Least effort, least regression risk, ships fastest.
- *Con:* Doesn't fix §1.3's actually-confusing user experience (a wall
  architect added still doesn't show up in "the" office view a Discord
  admin might reasonably expect to be singular) — it inherits the split
  brain, just makes it *one cog's* problem to route between two views
  instead of two cogs' problem to each serve their own.
- *Con:* "Unified dashboard" in the goal statement becomes true only in
  the hosting sense (one cog, one Dashboard entry point), not in the
  data sense (still two independent canvases underneath, reachable via
  two different pages/tabs within that one entry point).

**Option 3-unify: Migrate onto one canonical layout store,
`pixelagents.infrastructure.office_layout_settings` (already the newer,
more general design, already shared between architect and painter)
becoming the *only* office layout, with floorplan's own
`layout`/`seats` Config keys retired in favor of it.**

- *Pro:* Actually resolves §1.3 — one office, one set of walls/furniture/
  colors, visible identically whether it was changed via floorplan's
  in-browser editor, architect's `paint_tiles`, or painter's
  `recolor_tiles`. This is the more ambitious, more genuinely "unified"
  outcome the word in the goal statement could be read to imply.
- *Con:* **Major scope expansion, a second big design decision layered
  onto the hosting refactor.** Floorplan's layout is guild-scoped in UX
  (though not, per §1.3, actually partitioned that way at the Config
  level today) and its editor has real authorization
  (`TicketStore`/keyholder gating); architect's/painter's has none by
  design (`docs/architect-design.md` §5.1's explicit "anyone who can
  reach `/third-party/architect` should be able to freely edit it"
  decision). Unifying the stores means unifying — or explicitly
  reconciling — these two very different authorization models, not just
  the JSON blob underneath them. `docs/architect-semantic-ir-design.md`'s
  codec and floorplan's own raw layout format
  (`floorplan.contracts.layout.RawOfficeLayout`) would also need to be
  confirmed byte-compatible or bridged — not verified as part of this
  investigation, since it's out of scope for a hosting-only pass.
- *Con:* A real migration of live installations' layout data, the kind
  of breaking change `docs/architect-design.md` §2's LLM-provider move
  and `docs/painter-design.md`'s own precedent both treat as
  "no migration path, reconfigure once" *or* a careful one-time copy
  (`CogBase._migrate_legacy_layout`'s self-guarding-by-state pattern is
  the precedent to reuse if this is ever attempted) — either way, a
  decision a hosting refactor alone shouldn't have to make as a side
  effect.
- *Con:* Widens the pixelagents-charter-expansion tension already noted
  once in `docs/painter-design.md` §2 (floorplan's own settings module
  docstring: issue #21 deliberately moved a `layout` key *out* of
  pixelagents once already) — unifying onto pixelagents' store a second
  time, now absorbing floorplan's presence-linked layout too, is a
  bigger version of a move this repo has already reversed once.

**Recommendation shape:** Treat these as two separate design questions
with two separate PRs if both are ever pursued — 3-separate is the
option that actually matches "cctv unifies *hosting*, not data," is far
lower-risk, and can ship the goal as stated without waiting on a
data-model decision; 3-unify is a legitimate, larger follow-up worth its
own design doc (probably its own GitHub issue) rather than something
this refactor should bundle in.

### Axis 4 — how "still works without cctv" is structurally guaranteed

**Confirmed today (§1.5): write-tools already have zero hard dependency
on dashboard/broadcast success.** `paint_tiles`, `place_furniture`,
`recolor_tiles`, etc. all persist to Config first, unconditionally, and
only *then* attempt a best-effort broadcast to whatever `ClientHub`
happens to be running — which, with zero connected sockets (i.e. no
dashboard loaded at all), is simply zero recipients, not an error.
`painter`'s equivalent cross-cog notify hook
(`notify_shared_layout_changed()`) is likewise a `bot.get_cog(...)`
lookup that already tolerates the target cog being absent. **This
invariant does not need new code for any option above — it already
holds.** What each option changes is *who* owns the `ClientHub`/
`WebSocketServer` the broadcast targets:

- Under **1A/1C**, the broadcast callback each domain cog's
  `OfficeLayoutService`/`PainterLayoutService` is constructed with
  (`broadcast=self._broadcast_layout` in architect's `CogBase.__init__`,
  `on_layout_changed=self._notify_architect_layout_changed` in
  painter's) would need to become a **cross-cog, best-effort call into
  `cctv`** (`bot.get_cog("Cctv")`, `None`-checked, matching the existing
  painter→architect precedent exactly) instead of a call into a
  same-cog `ClientHub` instance. This is a bigger structural change than
  it first sounds: today `architect`'s own `_broadcast_layout` is
  guaranteed to exist (it's the same cog), so `self._broadcast is not
  None` in `OfficeLayoutService._persist` is really "was a callback
  provided at construction," never "is the target cog even loaded." Once
  the target is `cctv` — a cog that may not be loaded — the callback
  itself must become the kind of defensive, try/except-wrapped,
  never-fails-the-caller lookup `painter/adapters/cog_base.py:170-184`
  already demonstrates, not a plain method reference. **The precedent
  for exactly this shape already exists in this codebase and already
  ships (painter→architect) — this is "do it again, one more time, for
  cctv" rather than a new pattern.**
- Under **1B**, no change needed on this axis at all — each domain cog's
  broadcast callback keeps targeting its own, still-locally-owned
  `ClientHub`, since it never gave that ownership up.

Either way, the one thing worth flagging as a **new** risk (not present
today): under 1A/1C, if `cctv` is loaded, unloaded, and reloaded while a
domain cog is mid-mutation, the `bot.get_cog("Cctv")` lookup needs the
same "stale cog reference" defensiveness `dependency_loader.ensure_loaded`
callers already apply elsewhere in this repo — not because this is hard,
but because it's a new call site that must remember to apply an existing
convention, the same kind of easy-to-miss detail
`docs/painter-design.md`'s own implementation checklist flagged more
than once (mypy config, lint config, workflow matrix entries all needed
manual updates that were "found only by running the full-repo quality
gate, not by static review").

## 3. Summary table

| Axis | Options | This doc's read |
|---|---|---|
| 1. Where hosting code lives | 1A (fully in cctv) / 1B (shared lib, cogs keep own dashboards) / 1C (hybrid: stateless lib + cctv owns live state) | 1B doesn't meet the stated goal; 1A vs 1C is a "where does the file live" call, not a behavioral one |
| 2. How cctv learns state | 2A (direct `bot.get_cog`) / 2B (pubsub only) / 2C (pubsub + pull-on-load) | 2B alone regresses the cold-start behavior floorplan/architect already have today; 2A is simplest, 2C is more bus-native but costs more to build |
| 3. Unify the two layout stores? | 3-separate (cctv renders whichever store it's told) / 3-unify (one canonical store) | 3-separate matches "cctv unifies hosting, not data" and ships independently; 3-unify is a legitimate but much larger follow-up deserving its own design doc |
| 4. No-cctv guarantee | Already true for persistence (verified, §1.5); broadcast callback needs to become a `bot.get_cog("Cctv")`-shaped best-effort hook under 1A/1C, unchanged under 1B | Precedented by painter→architect's existing `notify_shared_layout_changed()` hook — replicate that shape, don't invent a new one |

## 4. What this doc deliberately does not decide

Per the task, no option above is chosen. Also explicitly not addressed,
left for whichever design doc follows a decision on the axes above:

- The exact shape of any new domain-cog-exposed pull method (Option 2A/2C)
  — return types, where it lives in each cog's layering.
- Whether `cctv` needs its own `required_cogs` on `floorplan`/`architect`/
  `painter` (probably not, if Axis 2 goes with pubsub-plus-`bot.get_cog`
  duck-typed lookups the way `pixelagents`→floorplan/architect already
  are today — `bot.get_cog` returning `None` for an unloaded cog is
  already the whole mechanism `docs/architecture.md` §1 documents as
  "operational, not coded" for the `toolbox`↔`pixelagents` edge).
- Whether painter's own recolor tools (still only investigated per issue
  #55, not implemented) change at all under any option here — nothing in
  this investigation found a reason they would; painter has no dashboard
  of its own today (`docs/painter-design.md` §7.1's checklist: "no
  WebSocket server, webview, Dashboard route... painter serves no
  browser-facing surface of its own") and none of the options above give
  it one, they only change who serves the *shared* office layout painter
  already reads/writes into.
- Command-surface changes (`[p]cctv ...`), `info.json` shape, or a
  migration/rollout plan for existing installations currently running
  floorplan's and/or architect's dashboards — all downstream of picking
  an option on Axis 1 and Axis 3 first.
