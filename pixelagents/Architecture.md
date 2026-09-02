# Pixelagents architecture

Pixelagents separates Pixel Agents schema knowledge from persistence and
browser hosting. Corridor stores opaque JSON-compatible aggregates;
CCTV owns the live browser runtime built from Pixelagents' bundle.

## Overview

Pixelagents is a leaf dependency four other cogs build on: Architect and
Painter mutate the `editor` aggregate through it, Floorplan mutates the
`discord` aggregate through it, and CCTV reads both kinds and serves the
webview bundle Pixelagents builds. None of the four ever reads or writes
Corridor's aggregate directly -- every access goes through Pixelagents'
`OfficeStateFacade`, so schema validation and the Semantic IR round-trip
happen exactly once, regardless of which cog is calling.

## Components and dependents

```mermaid
flowchart TB
    subgraph pixelagents
        adapters["adapters/cog_base.py<br/><small>cross-cog facade + bundle lifecycle</small>"]
        appstate["application/office_state.py<br/><small>OfficeStateFacade</small>"]
        approster["application/office.py, presence.py<br/><small>roster + activity projection</small>"]
        domain["domain/office_ir.py<br/><small>Semantic IR</small>"]
        contracts["contracts/layout.py, outbound.py<br/><small>wire schema + outbound messages</small>"]
        codec["infrastructure/pixel_agents_adapter.py<br/><small>IR codec</small>"]
        styles["infrastructure/furniture_styles.py"]
        build["infrastructure/webview_build.py"]
        settings["infrastructure/settings.py<br/><small>commit override Config</small>"]
    end

    corridor["corridor<br/><small>opaque state + lock + revisions</small>"]

    adapters --> appstate
    adapters --> approster
    adapters --> build
    adapters --> settings
    appstate --> contracts
    appstate --> codec
    appstate --> styles
    codec --> domain
    appstate -->|state / mutate / watch| corridor

    cctv["cctv"] -->|"office_state (discord + editor)<br/>webview_bundle_status"| adapters
    floorplan["floorplan"] -->|"set_office_layout (discord)"| adapters
    architect["architect"] -->|"office_state / set_office_layout (editor)"| adapters
    painter["painter"] -->|"office_state / set_office_layout (editor)"| adapters
```

`OfficeStateFacade` is the single schema-aware path. `set_layout` reads or
initializes the selected state, validates the candidate, and asks Corridor
for a layout-field update. `set_seats` and `mutate_seats` do the
equivalent for seats. Corridor performs the locked read-modify-write,
preserves the other field, increments the revision, then publishes the
complete post-write state.

For `OfficeStateKind.EDITOR`, layout validation also decodes and
re-encodes the Semantic IR using the current furniture manifest. This
makes malformed or unsupported editor state fail before persistence. The
Discord state needs the wire-layout validation only.

Both states initialize independently. If Corridor has no value, the
facade reads the bundled default, validates it for the chosen kind, and
calls Corridor's idempotent initializer. If the bundle/default is
unavailable, it raises `OfficeStateUnavailableError`. Existing invalid
state raises `OfficeStateValidationError`; neither case resets data.

## Key flows

### Office-state facade: validate, then delegate

Every write goes through the same read-current -> validate-candidate ->
delegate-to-Corridor shape, whether the caller is Architect placing
furniture or Floorplan saving a Discord layout:

```mermaid
sequenceDiagram
    participant Caller as architect / painter / floorplan
    participant Facade as OfficeStateFacade
    participant Codec as pixel_agents_adapter<br/>(editor kind only)
    participant Corridor as corridor<br/>(opaque state + lock)

    Caller->>Facade: set_layout(kind, raw)
    Facade->>Facade: state(kind) -- ensure it's initialized
    Facade->>Facade: validate_layout(kind, raw)<br/>OfficeLayout.model_validate
    alt kind == EDITOR
        Facade->>Codec: decode(layout, styles)
        Codec-->>Facade: Office (Semantic IR)
        Facade->>Codec: encode(Office, styles)
        Codec-->>Facade: re-encoded raw layout
    end
    Facade->>Corridor: set_office_layout(kind, layout)
    Corridor->>Corridor: locked read-modify-write,<br/>preserve seats, revision += 1
    Corridor-->>Facade: complete OfficeState
    Facade->>Facade: validate_state(...)
    Facade-->>Caller: OfficeState (layout, seats, revision)
```

A validation failure at any step raises before Corridor's
`set_office_layout` is ever called, so a rejected mutation never touches
persisted state.

### Webview build

`cog_load` builds the webview off the event loop, on a worker thread, the
first time it's needed:

```mermaid
sequenceDiagram
    participant Load as cog_load
    participant Build as webview_build.ensure_webview_built
    participant Cache as cog_data_path/webview_dist
    participant Git as git / npm / vite

    Load->>Build: ensure_webview_built(cog_data_path)
    Build->>Cache: is_up_to_date(commit, base_path, manifest_version)?
    alt cache is current
        Cache-->>Build: yes -- reuse existing webview_dist
    else stale or missing
        Build->>Git: clone/fetch pixel-agents-hq/pixel-agents at commit
        Build->>Git: npm ci --workspace=webview-ui --ignore-scripts
        Build->>Git: vite build --base ./
        Build->>Build: decode sprite assets,<br/>generate furniture-styles.json
        Build->>Cache: remove + repopulate webview_dist in place
    end
    Build-->>Load: BuildOutcome (ok / missing_tools / status_line)
```

The last step is not an atomic rename/swap: `_sync_dist` removes the
existing `webview_dist` (if any) and repopulates it file by file. An
`fcntl`-based file lock (`_build_lock`) still serializes concurrent build
attempts against the same `cog_data_path` so two builds never interleave
their writes, but a process crash mid-`_sync_dist` can leave a partial
`webview_dist` behind rather than rolling back to the previous build. A
failed build never fails `cog_load`: `webview_bundle_status()` reports the
failure and the owner is notified best-effort, but the cog stays loaded.

The relative `./` asset base lets CCTV serve one build beneath its own
static Dashboard route. `webview_bundle_status()` is a read-only surface
and never triggers a rebuild on its own.

## Contracts

`contracts/pixel_agents/verify.py` executes the real build into a
temporary directory, hands it to CCTV's production `WebviewAssets`,
verifies assets and the default layout, and checks outbound messages
against the pinned upstream AsyncAPI schema. The committed consumer
contract is generated from `pixelagents/contracts/outbound.py` and
checked separately for drift.

## Related docs

- [`README.md`](README.md) -- commands and configuration.
- [`docs/architect-semantic-ir-design.md`](../docs/architect-semantic-ir-design.md)
  -- the Semantic IR and Pixel Agents JSON codec this facade wraps.
- [`docs/architect-design.md`](../docs/architect-design.md) and
  [`docs/painter-design.md`](../docs/painter-design.md) -- how Architect
  and Painter each use the editor-kind facade.
- [`docs/cctv-design.md`](../docs/cctv-design.md) -- browser hosting built
  on `webview_bundle_status()` and both office-state kinds.
- [`docs/contract-testing.md`](../docs/contract-testing.md) -- how the
  build pipeline and outbound schema are contract-tested.
