# Floorplan architecture

Floorplan is the Pixel Index HTTP adapter cog. It normalizes and stores the
two Pixel Index endpoint URLs, wraps the public Pixel Index REST API behind
one lifecycle-managed `aiohttp` client, serves catalogue search/detail as
Discord commands and Corridor LLM tools, and authorizes + applies a selected
layout into Pixelagents' `discord` office-state aggregate. `cctv` owns every
browser-facing office responsibility (Dashboard pages, WebSocket transport,
guild scanning, presence projection); floorplan carries none of that.

## Components and dependencies

```mermaid
flowchart TB
    subgraph floorplan["floorplan"]
        direction TB
        subgraph domain_l["domain"]
            settings_d["settings.py<br/>normalize_http_url"]
        end
        subgraph app_l["application"]
            catalogue["catalogue.py<br/>CatalogueService"]
        end
        subgraph infra_l["infrastructure"]
            settings_i["settings.py<br/>RedSettingsRepository"]
            client["pixel_index.py<br/>PixelIndexClient"]
        end
        subgraph adapters_l["adapters"]
            cog_base["cog_base.py<br/>FloorplanBase"]
            admin_cmds["admin_commands.py"]
            catalogue_cmds["catalogue_commands.py"]
            views["layout_views.py<br/>Components V2 views"]
            tools["layout_tools.py<br/>tool schemas/output"]
            replies["replies.py"]
        end
        contracts["contracts/pixel_index.py<br/>pydantic response models"]
    end

    corridor["corridor<br/>permissions · reply rendering · LLM tool registry"]
    pixelagents["pixelagents<br/>office-state facade"]
    pixelindex[("Pixel Index HTTP API")]

    catalogue_cmds --> catalogue
    admin_cmds --> catalogue
    views --> catalogue
    tools --> catalogue
    catalogue --> settings_i
    catalogue --> client
    settings_i --> settings_d
    client --> contracts
    cog_base --> settings_i
    cog_base --> client
    cog_base --> catalogue
    replies --> corridor
    catalogue_cmds --> corridor
    cog_base --> corridor
    cog_base --> pixelagents
    client --> pixelindex
```

`application/catalogue.py` depends only on the `PixelIndexGateway` and
`CatalogueRepository` protocols it defines, plus two injected callables
(`can_edit_layout`, `apply_layout`) — it never imports `discord`, `redbot`,
or `corridor` directly. `adapters/cog_base.py` supplies those callables at
composition time: `_can_edit_layout_user` checks bot ownership then
Corridor's `keyholder` capability across every guild the bot can resolve the
member in, and `_apply_catalogue_layout` calls
`pixelagents.set_office_layout(OfficeStateKind.DISCORD, layout)` — always the
`DISCORD` aggregate, never the `EDITOR` aggregate that Architect and Painter
write.

`adapters/replies.py` renders every reply through Corridor's
`render_reply` (a `RenderedReply` DTO, not a raw send) and then does its own
ctx/interaction dispatch on top of that, because floorplan's hybrid
slash-command flows need ephemeral responses and deferred followups that
Corridor's higher-level `send_reply` doesn't cover. A command that replies
with a Components V2 `view=` (the catalogue browse/detail views) bypasses
rendering entirely and is sent as-is, since Discord rejects mixing
Components V2 with plain content or an embed.

## Key flows

### Catalogue search and view

```mermaid
sequenceDiagram
    participant U as Discord user
    participant F as floorplan adapters
    participant Svc as CatalogueService
    participant I as Pixel Index API

    U->>F: /floorplan layout search [query] [tag] [sort]
    F->>F: require_permission(employee)
    F->>Svc: search(query, tag, sort)
    Svc->>I: GET /api/v1/layouts
    I-->>Svc: layout list JSON
    Svc-->>F: CatalogueResult[LayoutListResponse]
    F-->>U: LayoutBrowseView (Components V2, paginated)

    U->>F: select a layout from the list
    F->>Svc: detail(slug)
    Svc->>I: GET /api/v1/layouts/{slug}
    I-->>Svc: layout detail JSON
    Svc-->>F: CatalogueResult[LayoutDetail]
    F-->>U: LayoutDetailView (preview, download/site links, Load button)
```

Both commands are also registered as Corridor LLM tools
(`floorplan_layout_search`, `floorplan_layout_view`) under the same
`employee` requirement; the tool handler returns the same JSON-safe summary
it renders into Discord, with the full layout blob omitted from the detail
output.

### Loading a layout into the Discord aggregate

```mermaid
sequenceDiagram
    participant U as Discord user
    participant V as LayoutDetailView
    participant Svc as CatalogueService
    participant C as corridor (owner / keyholder check)
    participant I as Pixel Index API
    participant P as pixelagents facade

    U->>V: click "Load into office"
    V->>Svc: load_layout(user_id, slug)
    Svc->>C: is_owner(user_id) or capabilities_satisfy(member, "keyholder")
    alt not authorized
        C-->>Svc: false
        Svc-->>V: CatalogueError(UNAUTHORIZED)
        V-->>U: "You are not authorized..." (ephemeral)
    else authorized
        C-->>Svc: true
        Svc->>I: GET /api/v1/layouts/{slug}
        I-->>Svc: layout detail JSON
        Svc->>P: set_office_layout(OfficeStateKind.DISCORD, layout)
        P->>P: validate Pixel Agents layout
        P-->>Svc: OfficeState at revision + 1 (seats unchanged)
        Svc-->>V: CatalogueResult("Loaded `<title>` into the office.")
        V-->>U: confirmation (ephemeral)
    end
```

The write touches only the `discord` aggregate's `layout` field; `seats` is
carried over unchanged, and the `editor` aggregate is never read or written.
If `cctv` is loaded, it receives the resulting `OfficeStateChanged` event
from Corridor and pushes the update to connected browsers over its own
WebSocket transport — that delivery path is entirely outside floorplan.

An invalid layout (rejected by Pixelagents' validation) or an unreachable/
malformed Pixel Index response surfaces as a `CatalogueError` with a stable
`CatalogueErrorCode` (`timeout`, `transport`, `http_status`, `invalid_json`,
`invalid_response`, `unauthorized`, `invalid_layout`), which every adapter —
Discord reply, Components V2 view, and LLM tool output — renders as the same
user-safe message.

## Configuration and the Pixel Index contract

The Config repository stores exactly `pixel_index_api_url` and
`pixel_index_web_url` under a Config identity scoped to this cog. The Pixel
Index consumer contract is generated from `floorplan/contracts/pixel_index.py`
and the endpoint registry under `contracts/pixel_index/`; see
[`docs/contract-testing.md`](../docs/contract-testing.md).

See [PERMISSIONS.md](PERMISSIONS.md) for exactly which permission tier each
command and the load button require.
