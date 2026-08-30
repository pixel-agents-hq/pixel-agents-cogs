# painter design

Tracks [issue #55](https://github.com/pixel-agents-hq/pixel-agents-cogs/issues/55),
"Add new cog 'painter'". This doc covers the whole change as agreed with
the repo owner during design review — a single PR, in three parts that
must land together: (A) extracting architect's Semantic IR out of
`architect` into `pixelagents` so a second cog can reach it, (B) adding
wall color to that IR, and (C) the new `painter` cog itself. See
[`docs/architect-semantic-ir-design.md`](architect-semantic-ir-design.md)
for the IR as it exists today (pre-extraction) and
[`docs/architecture.md`](architecture.md) for the cross-cog picture this
doc extends.

## 0. Deliberate departures from the issue as filed

Two decisions here contradict the issue's own text. Recorded explicitly,
not silently reconciled:

- **No model vision in v1.** The issue frames painter as fetching
  `preview.png` and visually judging color from the rendered image.
  Confirmed with the repo owner: v1 has no vision capability at all.
  Painter infers/reads color entirely through tool calls against
  structured data (§3), never an image. This makes the issue's own open
  question ("clarify if it makes sense to implement this as an LLM tool
  response... painter's context is the image and a description") moot —
  there is no image in this iteration.
- **A2A-only, not Discord-facing.** The issue says painter "may answer
  the discord user" directly. That conflicts with this repo's documented
  convention (`docs/AGENTS.md`, `docs/architecture.md`): pico is "the
  sole A2A coordinator," and every other agent (today just `architect`)
  is A2A-only, invoked by pico, never Discord-facing itself. Confirmed
  with the repo owner: painter follows architect's shape — A2A-only,
  registered on corridor's shared A2A listener, invoked via pico's
  `consult_painter` tool exactly the way `consult_architect` already
  works. Pico still composes the Discord-facing reply.

## 1. Scope for v1

In scope:

- Recolor a single floor tile / region of floor tiles.
- Recolor a single wall tile / region of wall tiles (§5 — new capability,
  the underlying format already supports it, architect's decoder just
  discards it today).
- Recolor a single furniture item.
- Bulk recolor: every furniture item of a given kind+style at once (from
  the issue comment's tool ideas).
- Read tools sufficient for painter's own LLM to reason about "what
  colors are currently in use" without any image.

Out of scope for v1 (candidates for later issues):

- Any vision/image capability (`preview.png` fetch, pixel index API
  integration) — v1 attaches no image to its answer.
- Anything structural: adding/removing/moving furniture, converting
  floor↔wall, resizing/creating zones. Painter never gets architect's
  `paint_tiles`/`place_furniture`/`move_furniture`/`remove_furniture`
  tools or their shape — see §6.3 for why `paint_tiles` specifically
  can't just be narrowed and handed to painter.
- Discord-facing conversation loop, its own presence/appearance
  configuration beyond what corridor's agent directory already gives
  every registered agent.
- Any change to what architect's own LLM does or which tools it calls —
  see §0 and §4.

## 2. Package relationships

```mermaid
flowchart BT
    corridor["corridor<br/><small>permissions + reply style + PubSub bus<br/>+ shared LLM connection<br/>+ shared A2A listener/directory<br/>hidden COG</small>"]
    pixelagents["pixelagents<br/><small>vendors + builds the webview<br/><b>NEW:</b> owns the Semantic IR domain<br/>model, Pixel Agents JSON codec,<br/>color palette, and the one office<br/>layout's Config-backed storage</small>"]
    architect["architect<br/><small>A2A-only, second LLM agent<br/>structural mutations (paint_tiles,<br/>place_furniture, move_furniture,<br/>remove_furniture, zones)<br/>reads/writes the layout via a thin<br/>pass-through into pixelagents' store</small>"]
    painter["painter<br/><small><b>NEW</b> — A2A-only, third LLM agent<br/>color-only mutations (recolor_tiles,<br/>recolor_furniture, bulk recolor)<br/>reads/writes color via its own<br/>service on pixelagents' shared store<br/>reads structure via A2A to architect</small>"]
    pico["pico<br/><small>LLM-backed Discord presence,<br/>sole A2A coordinator</small>"]

    architect -->|required_cogs| corridor
    architect -->|required_cogs| pixelagents
    painter -->|required_cogs| corridor
    painter -->|required_cogs| pixelagents
    pixelagents -->|required_cogs| corridor
    pico -->|required_cogs| corridor
    architect -.->|"register_agent()<br/>(in-process, via corridor)"| corridor
    painter -.->|"register_agent()<br/>(in-process, via corridor)"| corridor
    pico -.->|"A2A over HTTP, one shared port<br/>consult_architect / consult_painter"| corridor
    painter -.->|"A2A over HTTP<br/>consult_architect (structural reads only)"| corridor
```

Notes:

- `painter -> pixelagents` and `architect -> pixelagents` are both
  `required_cogs` (in-process import + `ensure_loaded`), same pattern
  architect already uses for pixelagents' webview build today.
  `painter -> architect` is deliberately **not** a `required_cogs` edge —
  it is a networked A2A call, the same "networked, not coded" shape
  `pico -> architect` already is (`docs/architect-design.md` §7):
  painter degrades to "the `consult_architect` tool errors" if architect
  is unloaded/unreachable, it does not fail to load.
- This is a real expansion of `pixelagents`' documented charter — more
  than a general "it says it owns nothing runtime-facing" tension: issue
  #21 already tried a `layout` Config key on this exact cog once and
  deliberately moved it *out*, to `floorplan`
  (`pixelagents/infrastructure/settings.py`'s own docstring: "the runtime
  settings that used to share this identifier (ws_port, **layout**,
  seats, guild enabled/include_bots, pixel_index urls, ...) now live in
  floorplan's own Config store"). This plan revives that same key name on
  the same cog. Confirmed explicitly with the repo owner after surfacing
  this precedent directly — not overlooked, a deliberate choice made
  because: (a) it's already the one cog both `architect` and `painter`
  need as a dependency regardless, (b) unlike floorplan's office (which
  mirrors Discord presence and is fed by the pixel index API, and is
  guild-scoped), architect's/painter's shared office is a distinct,
  independent, global (not per-guild) layout with no presence-mirroring
  concerns — narrower and more mechanical than the guild-scoped bundle
  #21 reacted against, even though it reuses the same key name. (c) the
  layout with no presence-mirroring concerns, and (c) the alternative
  (painter reaching into architect's private Config identifier directly,
  or a new fourth package) was explicitly rejected in review.

## 3. End-to-end flow

Example: a Discord user asks pico to make the northeast room "warm and
cozy."

```mermaid
sequenceDiagram
    participant User as Discord user
    participant Pico as pico
    participant Painter as painter (A2A)
    participant Architect as architect (A2A)
    participant PA as pixelagents<br/>(shared Office IR + store)

    User->>Pico: "make the NE room warm and cozy"
    Pico->>Painter: consult_painter(prompt) [A2A]
    Painter->>Architect: consult_architect(prompt) [A2A]
    Note right of Architect: architect's own LLM tool loop<br/>runs describe_office/describe_tiles/<br/>find_furniture — unchanged, §0/§4
    Architect-->>Painter: prose: what's in the NE room<br/>(kinds, positions, styles — no color)
    Painter->>PA: read current tile/furniture colors<br/>(painter's own tools, §6.2)
    PA-->>Painter: structured TileCell/FurnitureItem data<br/>(color field, semantic names)
    Note right of Painter: painter's own LLM decides<br/>new colors — no image, no prose<br/>re-interpretation for color itself
    Painter->>PA: recolor_tiles / recolor_furniture<br/>(painter's own tools, §6.2)
    PA-->>Painter: ok
    Painter-->>Pico: prose: what was repainted
    Pico-->>User: final reply (ReplyTool)
```

Key property this preserves: **color judgment happens entirely inside
painter's own LLM, against structured data painter reads/writes itself.**
Architect is only ever asked "what/where," never "what color," and never
gains new tools or new judgment calls — it stays exactly as described in
§0.

## 4. What does *not* change in architect

Confirmed explicitly with the repo owner: architect's own LLM, system
prompt, and tool surface (`describe_office`, `describe_tiles`,
`find_furniture`, `paint_tiles`, `place_furniture`, `move_furniture`,
`remove_furniture`, `create_zone`, ...) are unchanged. It remains
"colorblind" — painter never delegates a color decision to it. The only
change on architect's side at all is mechanical: `office_layout_repository.py`
becomes a thin pass-through to pixelagents' store (§5.3) instead of
architect's own private Config identifier, and its decode/encode calls
resolve to pixelagents' module instead of a local one. No new architect
tools, no system-prompt changes, no new A2A behavior.

## 5. Part A — extract the Semantic IR into `pixelagents`

### 5.1 What moves

| Today (architect) | Moves to (pixelagents) | Why |
|---|---|---|
| `architect/domain/office_ir.py` | `pixelagents/domain/office_ir.py` | The `Office`/`TileCell`/`FurnitureItem`/`Zone`/`Seat`/... dataclasses `decode`/`encode` produce and consume — can't move the codec without the types it speaks. |
| `architect/infrastructure/pixel_agents_adapter.py` | `pixelagents/infrastructure/pixel_agents_adapter.py` | The `decode`/`encode` functions themselves — the actual Pixel Agents JSON ⟷ IR codec. |
| `architect/infrastructure/color_names.py` | `pixelagents/infrastructure/color_names.py` | `decode()` calls `nearest_name()` directly; the semantic palette is intrinsic to the codec, not architect-specific. |
| Layout half of `architect/infrastructure/settings_repository.py` (`layout()`/`set_layout()` + the Config identifier's layout keys) | `pixelagents/infrastructure/office_layout_settings.py` (new) | The raw JSON blob's storage location — needs a home neither cog privately owns, so both can reach it without one depending on the other's Config partition. |

**Stays in architect** (nothing here moves): `architect/application/office_layout_service.py`
(structural mutation logic — `paint_tiles`, `place_furniture`,
`move_furniture`, `remove_furniture`, `create_zone`, etc.), `architect/tools/office_tools.py`
(architect's own LLM tool wrappers), `architect/infrastructure/furniture_styles.py`
(furniture style manifest — architect-specific, painter only needs style
*names* it already gets from architect's A2A reads, not the manifest
itself), architect's own non-layout settings (`max_tool_calls`,
`system_prompt`, `ws_host`/`ws_port`, `debug_logging`).

### 5.2 New pixelagents layering (as implemented)

- `pixelagents/domain/office_ir.py` — moved as-is (plus the wall-color
  field, §6), re-exported through `pixelagents/domain/__init__.py`
  alongside its existing `office.py`/`settings.py` exports.
- `pixelagents/infrastructure/pixel_agents_adapter.py` — moved as-is
  (plus the wall-color decode/encode fix, §6). Its own relative imports
  of the domain/palette/style-manifest modules needed no changes — they
  already resolve correctly once everything sits under the same package.
- `pixelagents/infrastructure/color_names.py` — moved as-is.
- `pixelagents/infrastructure/furniture_styles.py` — moved as-is, **not
  originally planned** (§0's investigation-time addendum below covers
  why). `FurnitureStyleManifest`/`FurnitureFacing`/`FurnitureStyle`
  (pure data) and `FurnitureStyleLoader`/`SupportsFurnitureStyles` (a
  generic cache keyed off any `webview_bundle_status()`/
  `furniture_style_manifest()`-shaped object — no architect-specific
  state at all) turned out to have zero architect coupling either.
- `pixelagents/infrastructure/office_layout_settings.py` — new.
  `RedOfficeLayoutSettings`, owning a freshly-rolled Config identifier
  (distinct from `settings.py`'s own) for the one shared office layout
  blob. `create()` takes no cog instance — `Config.get_conf(None,
  identifier=..., cog_name="pixelagents")` — so architect's and
  painter's independently-constructed instances resolve to the same
  on-disk store without either needing a live reference to the other or
  to a loaded `PixelAgents` Cog.
- No separate `pixelagents/application/office_repository.py` ended up
  necessary — `RedOfficeLayoutSettings` alone already satisfies the
  `SupportsLayoutStorage` Protocol (`layout()`/`set_layout()`)
  `architect/infrastructure/office_layout_repository.py` already defined
  and depended on, so that existing class needed no restructuring at
  all, only an import-path fix (§5.3) and a different settings object
  handed to its constructor.
- `pixelagents/tests/test_architecture.py`'s
  `test_framework_resources_have_one_owner_each` contract test (renamed
  from `..._have_one_owner`) now allows exactly these two
  `Config.get_conf(` call sites, not one — updated deliberately, not
  loosened by accident.

**Addendum found during implementation, not anticipated in review:**
`pixel_agents_adapter.py`'s `decode()`/`encode()` take a
`FurnitureStyleManifest` as a required argument — so *any* caller of the
shared codec, painter's own repository included, needs one, contradicting
this doc's original "painter only needs style names from architect's A2A
reads, not the manifest itself." Since `FurnitureStyleManifest`/
`FurnitureStyleLoader` had zero architect-specific coupling to begin
with, moving the whole file was the natural fix, not a competing design
worth a design-review stop — flagged here for the record rather than
silently done.

### 5.3 Architect's side after extraction

`architect/infrastructure/office_layout_repository.py`'s public shape
(`OfficeLayoutRepository.load`/`save`/`decode_raw`,
`OfficeLayoutNotSeededError`) is **completely unchanged** — only its
imports of `Office`/`FurnitureStyleManifest`/`decode`/`encode` now point
at `pixelagents` instead of local sibling modules — so
`application/office_layout_service.py` needed **zero changes** at all, as
planned. What changed is one layer up, in `adapters/cog_base.py`'s
composition: it now constructs `RedOfficeLayoutSettings.create()` and
hands *that* to `OfficeLayoutRepository(...)` instead of architect's own
`RedArchitectRepository`. `architect/infrastructure/settings_repository.py`
dropped `layout()`/`set_layout()` (replaced with migration-only
`legacy_layout()`/`clear_legacy_layout()`, see below) and kept everything
else (`max_tool_calls`, `system_prompt`, `ws_host`/`ws_port`,
`debug_logging`) — its `"layout"` Config key stays registered, read-only,
purely so migration can still reach it.

**Migration**: `CogBase._migrate_legacy_layout()`, called once per
`cog_load()`. Self-guarding by state, not a separate flag: it only copies
`legacy_layout()` across when the *new* store is still empty and the
*old* one has something, so a second `cog_load()` (or a second `Architect`
instance) is a no-op — no dedicated "already migrated" marker needed. A
migration failure is caught and logged, never blocks `cog_load()`.
Two other call sites also read/wrote architect's old `layout` key
directly and needed the same fix: `adapters/commands.py`'s `[p]architect
status` (now reads `self._office_layout_settings.layout()`) and
`adapters/office_gateway.py`'s `_current_layout()` (same) — both found by
running the full test suite, not by static review, so worth a second
read-through of anything else touching `self._repository.layout`-shaped
calls if this doc is used as a checklist for a similar future move.

### 5.4 Painter's side

`painter/infrastructure/office_repository.py` (or painter simply imports
`pixelagents.application.office_repository` directly — a design-time
choice for implementation, not this doc) gives painter the same
`load()`/`decode_raw()` access as architect, read-only for structural
fields and read/write for color fields only (enforced by painter's own
application-layer service, §6.2 — the repository itself doesn't know
about "color-only," that restriction lives in painter's service, the
same way architect's own restrictions live in its service, not its
repository).

## 6. Part B — wall color

### 6.1 The gap

`TileCell.color` is documented as floor-only
(`docs/architect-semantic-ir-design.md` §6.3, and `TileCell.wall()`
hard-codes `color=None`). `pixel_agents_adapter.py`'s `_build_grid()`
never reads `tile_colors[i]` for a `WALL` cell. This was verified against
the actual upstream renderer
(`~/pixel-agents/webview-ui/src/office/wallTiles.ts`,
`.../engine/renderer.ts`): walls read `tileColors[colorIdx]` at the exact
same index as floor tiles, same `{h,s,b,c}` shape, same colorize sprite
pipeline (`getColorizedWallSprite`/`wallColorToHex`). Wall color is real
and renders; architect's decoder just discards it. The semantic design
doc's "`null` on walls/void" note is stale relative to the current
upstream format and should be corrected in the same PR.

### 6.2 The fix (in `pixelagents/domain/office_ir.py` + `.../infrastructure/pixel_agents_adapter.py`, post-move)

- `TileCell.color`/`raw_color` become populated for `WALL` cells too —
  `TileCell.wall()` gains an optional `color`/`raw_color` parameter
  (defaulting to `None`, so every existing call site that doesn't pass
  one is unaffected).
- `_build_grid()`'s `if value == _WALL:` branch reads `tile_colors[i]`
  exactly like the floor branch already does, instead of always passing
  `zone_label` only.
- The encode direction (`office_ir.py` → raw JSON) writes wall cells'
  `color`/`raw_color` into `tileColors[i]` the same way floor cells'
  already are, instead of unconditionally emitting `null` for wall
  positions.
- Lossless round-trip contract (`docs/architect-semantic-ir-design.md`
  §6.1/§10, `architect/tests/test_lossless_round_trip.py`): an untouched
  wall cell with an existing color must re-encode to the exact original
  hex, via the same `raw_color`-preservation mechanism floor cells
  already use. This needs the round-trip test extended with a
  colored-wall fixture.

### 6.3 Why architect's own `paint_tiles` still can't be reused as-is

`paint_tiles(kind: "floor"|"wall", ...)` is architect's *structural*
primitive — it converts a region between floor and wall, i.e. it can
build and destroy walls. Painter must never do that (§1). Painter's own
recolor tool is new, narrower service logic (§7) that requires the
target cells already be the kind being recolored (floor stays floor,
wall stays wall) and only ever touches `color`/`raw_color` — it has no
`kind` or `material` parameter at all.

## 7. Part C — the `painter` cog

### 7.1 Scaffold

Generated from `.cookiecutter/cog-cookiecutter` (never hand-written, per
this repo's CLAUDE.md), `cog_name=painter`. `required_cogs`: `corridor`,
`pixelagents`. Standard layering:

- `painter/domain/` — pure logic: color-selection helpers if any turn out
  not to be pure LLM judgment (e.g. "suggest a warm palette" heuristics),
  otherwise this layer may end up thin/empty for v1 and that's fine.
- `painter/application/painter_layout_service.py` — the color-only
  mutation surface (§7.3), built on `pixelagents`' shared `Office`
  IR/repository (§5.4).
- `painter/infrastructure/architect_client.py` — an `AgentAsker`
  mirroring pico's `ArchitectClient`/`ArchitectAsker` exactly (same A2A
  transport, `ask(base_url=..., text=...)` shape) — painter's own LLM
  tool loop gets a `consult_architect`-equivalent tool for structural
  reads (§7.2).
- `painter/adapters/cog_base.py` — composition root: `ensure_loaded` for
  `pixelagents`, `ensure_corridor_loaded`, builds+registers painter's
  `AgentCard`/`AgentExecutor` with corridor (`agent_key="painter"`,
  mirrors `architect/adapters/cog_base.py`'s `_register_with_corridor`
  exactly).
- `painter/infrastructure/a2a_server.py` — `PainterAgentExecutor`,
  mirrors `architect/infrastructure/a2a_server.py`'s shape (runs
  painter's own bounded tool-calling loop per inbound A2A message from
  pico).
- `painter/tools/painter_tools.py` — the LLM tool wrappers (§7.2/§7.3).

Once painter registers with corridor, pico picks it up automatically —
`ConsultAgentTool` already builds one instance per entry in
`corridor.list_agents()` each turn (`pico/adapters/listener.py`), so
`consult_painter` requires zero pico-side code changes, same as every
future agent.

### 7.2 Read tools (structural, via A2A to architect)

Painter's tool loop gets one tool, `consult_architect` — structurally
identical to pico's `ConsultAgentTool`/`ArchitectAsker` (`prompt: str` in,
`answer: str | None` out), reused as its own `AgentAsker` Protocol
implementation against the same architect A2A endpoint. Painter's system
prompt teaches its LLM to use this for "what furniture/tiles/walls exist
and where," never for color (architect has none to give, §4).

### 7.3 Read/write tools (color, direct via pixelagents)

New tools, painter-owned, no architect equivalent:

| Tool | Shape | Notes |
|---|---|---|
| `describe_colors` | Input: optional area/furniture filter. Output: per-tile and per-furniture `color` (semantic name), mirroring `DescribeTilesOutput`/`FindFurnitureOutput`'s existing `color` field shape. | Painter's structured, no-vision color read — the direct replacement for the issue's image-based color judgment. |
| `recolor_tiles` | Input: area (col/row + width/height or end_col/end_row, same shape as `PaintTilesInput` minus `kind`/`material`), `color: str`. | Refuses if any cell in the area isn't already the kind it currently is meant to stay (floor stays floor, wall stays wall) — no structural conversion possible, by construction (no `kind` param exists). |
| `recolor_furniture` | Input: `furniture_id: str`, `color: str`. | New service method — `FurnitureItem.color` has no writer anywhere today (§ investigation finding); this is genuinely new capability, not a narrowed existing one. |
| `recolor_furniture_by_style` | Input: `kind`, `style`, `color: str`. | Bulk recolor, from the issue comment's tool ideas — every placed item of that kind+style at once. |

All four validate `color` against the same `known_names()` palette
architect's `paint_tiles`/`create_zone` already validate against
(moved to `pixelagents/infrastructure/color_names.py`, §5.1) — painter
never invents colors outside that palette.

### 7.4 What painter's tool surface deliberately excludes

No `place_furniture`, `move_furniture`, `remove_furniture`, or any
`kind`/`material` parameter anywhere — painter cannot add, remove, move,
or structurally convert anything, enforced by the tool schemas
themselves having no such fields, not just by prompt instruction.

## 8. Open risks / follow-ups

- **Concurrent writes.** Architect and painter now both read/write the
  same pixelagents-owned Config blob independently (no shared in-process
  lock beyond whatever `Config`'s own driver already serializes). A race
  between an architect structural edit and a painter recolor landing at
  the same moment is a last-write-wins overwrite of the whole blob, same
  as today's single-writer architect-only case just now with two
  writers. Acceptable for v1 (matches the "no session/lock concept"
  decision already made for the in-browser editor,
  `docs/architect-design.md` §5.1), flagged here for whoever revisits it.
- **Migration correctness** (§5.3) needs a real test against a
  pre-extraction Config snapshot, not just unit tests of the new code.
- **Wall-color palette UX**: once painter can set arbitrary
  `known_names()` colors on walls, worth eyeballing the rendered result
  in `~/pixel-agents`'s webview during implementation — the wall sprite's
  colorize pipeline was built for the wall texture's own tone range, not
  validated against every semantic palette entry.
- pixelagents' `Architecture.md`/`README.md` need a documentation update
  reflecting its new IR/storage ownership (§2's charter note) — tracked
  as a checklist item below, not done in this design doc.

## 9. Implementation checklist

### Part A — extract Semantic IR into pixelagents
- [x] Move `office_ir.py` to `pixelagents/domain/`
- [x] Move `pixel_agents_adapter.py` to `pixelagents/infrastructure/`
- [x] Move `color_names.py` to `pixelagents/infrastructure/`
- [x] Move `furniture_styles.py` to `pixelagents/infrastructure/` (not originally planned — §5.2 addendum)
- [x] New `pixelagents/infrastructure/office_layout_settings.py` (Config identifier + `layout()`/`set_layout()`)
- [x] ~~New `pixelagents/application/office_repository.py`~~ — turned out unnecessary, `RedOfficeLayoutSettings` alone satisfies `SupportsLayoutStorage` (§5.2)
- [x] `architect/infrastructure/office_layout_repository.py` import paths updated (its own logic untouched); `application/office_layout_service.py` unchanged
- [x] `architect/infrastructure/settings_repository.py` drops `layout()`/`set_layout()` (kept `legacy_layout()`/`clear_legacy_layout()` for migration), keeps the rest
- [x] One-time migration on architect `cog_load` (`_migrate_legacy_layout`, self-guarding by state)
- [x] `architect`'s `required_cogs` gains nothing new (already depends on pixelagents)
- [ ] Update `pixelagents/Architecture.md` and `README.md` for the new IR/storage ownership
- [ ] Update `docs/architecture.md` and `docs/AGENTS.md` dependency graph/descriptions
- [x] All existing architect tests pass (import paths updated; `test_office_ir.py`/`test_pixel_agents_adapter.py`/`test_color_names.py`/`test_furniture_styles.py` moved to `pixelagents/tests/` since they test code that physically moved)
- [x] New/updated pixelagents-side tests: moved suites above, plus `test_architecture.py`'s `test_framework_resources_have_one_owner_each`
- [x] New architect-side tests: `TestLegacyLayoutMigration` (`test_layout_seeding.py`), updated `test_settings_repository.py`
- [x] `ruff format`/`ruff check`/`mypy` clean, full `architect`+`pixelagents` suites green (265 + 183 passed)

### Part B — wall color
- [x] `TileCell.wall()` accepts optional `color`/`raw_color`
- [x] `_build_grid()` reads `tile_colors[i]` for `WALL` cells
- [x] Encode direction writes wall `color`/`raw_color` back into `tileColors[i]`
- [x] `docs/architect-semantic-ir-design.md` §6.3's "FLOOR only" note corrected
- [x] `test_pixel_agents_adapter.py` gains wall-color decode/encode/round-trip tests; `test_lossless_round_trip.py` gains a colored-wall fixture through the full service stack
- [ ] Manual check against `~/pixel-agents` webview that a repainted wall actually renders the new color

### Part C — painter cog
- [x] Scaffold via `.cookiecutter/cog-cookiecutter`
- [x] `painter/infrastructure/architect_client.py` (`ArchitectClient`, parallel copy of pico's own, generic by design)
- [x] `painter/tools/consult_architect_tool.py` (`ConsultArchitectTool` -- resolves architect's URL from `corridor.list_agents()` fresh every call, since painter only ever consults this one known agent, unlike pico's one-tool-per-registered-agent shape)
- [x] `painter/infrastructure/office_layout_repository.py` (painter's own copy, reads/writes pixelagents' shared store)
- [x] `painter/application/painter_layout_service.py` (color-only mutation surface: `describe_tile_colors`/`describe_furniture_colors`/`recolor_tiles`/`recolor_furniture`/`recolor_furniture_by_style` -- split `describe_colors` into two tools rather than one combined one, a minor refinement of this table's original shape)
- [x] `painter/tools/painter_tools.py`: five tools, `RecolorTilesTool` mirrors `paint_tiles`'s width/height-or-end_col/end_row convention
- [x] `painter/infrastructure/a2a_server.py` (`PainterAgentExecutor`), `painter/application/tool_loop_service.py`, `painter/infrastructure/corridor_llm.py`, `painter/tools/base.py`, `painter/tools/agent_tool_server.py` -- all parallel copies of architect's own equivalents
- [x] `painter/adapters/cog_base.py`: registers `agent_key="painter"` with corridor; no WebSocket server, webview, Dashboard route, or presence-tracking mixin (painter serves no browser-facing surface of its own)
- [x] Painter's system prompt: use `consult_architect` for structure, own tools for color, never invent colors outside `known_names()`
- [x] `pyproject.toml`'s `[tool.mypy]` `files`/`exclude` lists and per-module overrides gained a `painter` entry -- found only by running the full-repo quality gate, not by static review; easy to miss when adding a new cog
- [x] `contracts/discord_replies/lint_reply_channel.py`'s `COG_PACKAGES` gained `"painter"` -- its own comment says "add a new cog here when it's created"
- [x] `.github/workflows/cogs-quality.yml` gained a `painter` matrix leg (`extra_deps` is the union of architect's, an A2A server, and pico's, an A2A client) and `"painter/**/*.py"` in both trigger path lists
- [x] `painter/info.json`'s `requirements` gained the same union (`aiohttp`, `httpx`, `pydantic` pin, `a2a-sdk`, `typing-inspection` pin)
- [x] `contracts/pixel-agents-consumer-contract.yaml` / corridor's agent-directory contract -- checked, neither references agent names statically (`register_agent` is a runtime call), nothing to update
- [x] `docs/architecture.md`, `docs/AGENTS.md`, `.claude/CLAUDE.md` updated to list painter as a third A2A agent and add it to every per-cog command list; `docs/agent-directory-design.md` not touched (its own design is already generic across "any future agent," painter needed no new content there)
- [x] New `painter/README.md`, `painter/Architecture.md` (real content, not cookiecutter placeholders)
- [x] Full test suite for painter: 103 tests (settings repository, tool loop service and A2A server as parallel copies of architect's own test suites, plus new tests for the layout repository, layout service, tool wrappers, and consult_architect tool)
- [x] Verify `consult_painter` appears in pico automatically once painter registers -- confirmed by code inspection (`pico/adapters/listener.py`'s `_agent_tools` builds one `ConsultAgentTool` per `corridor.list_agents()` entry, no pico-side code touched), not exercised by a live integration test in this PR
