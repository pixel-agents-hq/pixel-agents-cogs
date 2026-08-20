# Consumer-driven contract testing

office-cogs depends on two external Pixel Agents projects it doesn't own: the
[Pixel Index](https://github.com/pixel-agents-hq/index) HTTP API and the
[Pixel Agents](https://github.com/pixel-agents-hq/pixel-agents) webview source
pixelagents vendors and builds. Neither project can gate its own releases on
this repo's needs alone (both have other consumers), and neither can be
trusted not to drift out from under office-cogs. Both dependencies get the
same treatment: **office-cogs owns the contract**, generated from the same
code that actually depends on the external project, checked live on a
schedule and on relevant PRs, and published to a shared status site so a
break is visible before it reaches a bot host.
[`.github/workflows/contract-checks.yml`](../.github/workflows/contract-checks.yml)
runs both checks and publishes both results together — see "Why one
workflow" under Pixel Agents below for why they aren't split into two.

## Pixel Index

The Pixel Agents catalogue integration (`floorplan`) talks to Pixel Index over
plain HTTP — there's no shared package between the two repos, just a
configurable base URL (`[p]floorplan index set`). That's deliberate: office-cogs shouldn't
hard-depend on pixel-index's code, but it does hard-depend on pixel-index's
API *shape*. This section explains how office-cogs catches a breaking shape
change before it breaks the bot.

### The model

This is consumer-driven contract testing: **office-cogs owns the contract**,
not pixel-index. And within office-cogs, the contract isn't hand-written —
it's generated from the same models the bot uses to parse responses, so it
can't drift from what the code actually depends on.

- [`floorplan/contracts/pixel_index.py`](../floorplan/contracts/pixel_index.py) —
  pydantic models
  describing only the fields the catalogue service and Discord views read from Pixel Index's
  layout list/detail responses. This is the real source of truth: fields
  the bot reads defensively (`entry.get("furniture", 0)`) are optional here;
  fields it depends on unconditionally (`slug`) are required. It's imported
  by two things:
  1. **The catalogue runtime** — `PixelIndexClient` validates every real
     search/detail response against these models before returning it, so a
     shape change becomes a classified, user-safe error instead of a
     `KeyError`/`AttributeError` deep in a Discord view.
  2. **The contract generator at CI time** — same models, same meaning,
     used to build the JSON Schema that gets checked against a live
     environment.
- [`contracts/pixel_index/endpoints.py`](../contracts/pixel_index/endpoints.py)
  — the part that genuinely can't be derived from code: which endpoints get
  called, what query params, and how they chain (`list_layouts` derives a
  real slug for `layout_detail` to use, so we're never testing against
  hardcoded fixture data). Each entry points at the model that describes its
  response, if any.
- [`contracts/pixel_index/generate_contract.py`](../contracts/pixel_index/generate_contract.py)
  — combines the two into `contract.yaml` by calling `.model_json_schema()`
  on each endpoint's model. **`contract.yaml` is a build artifact: gitignored,
  regenerated on every run, never hand-edited.** Run it directly to inspect
  the generated schema: `python -m contracts.pixel_index.generate_contract`.
- [`contracts/pixel_index/verify.py`](../contracts/pixel_index/verify.py) —
  regenerates the contract, then calls each endpoint for real against a
  target base URL and validates the live response against the generated
  schema.

A pass means "this environment is safe for office-cogs to consume right
now." A fail means something office-cogs reads has changed shape, and the
bot should not be pointed at that environment until the catalogue client,
service, and models are reconciled with reality.

#### Why generate instead of hand-write the schema

An earlier version of this hand-wrote `contract.yaml`. It missed fields
the integration actually reads (`furniture`, `visibleCols`, `areas`, `pets`,
`seats` were absent from the first draft) simply because nobody re-read the
whole file top-to-bottom while writing the YAML by hand. Generating the
schema from the same models that parse the response at runtime means the
contract can't fall out of sync with the code the way hand-maintained
duplication can — there's exactly one description of "what we depend on,"
and both the bot and the CI check read it.

### Catching drift before it reaches contract.yaml

The generated contract only covers what's registered in `endpoints.py` and
modeled in `contracts/pixel_index.py` — it can't warn about a call site or a
field that was never added there in the first place. Two lint checks close that gap, run on
every PR that touches Python code under `floorplan/` or anything under
`contracts/pixel_index/` (see
[`.github/workflows/pixel-index-contract-lint.yml`](../.github/workflows/pixel-index-contract-lint.yml)):

- [`contracts/pixel_index/lint_endpoints.py`](../contracts/pixel_index/lint_endpoints.py)
  — **new endpoint, not registered.** Every JSON endpoint is called through
  the single `self._pixel_index_get(path)` chokepoint, so this walks every
  production module in the package for those call sites (including f-string path
  templates like `f"/api/v1/layouts/{slug}"`), and fails if a called path
  isn't in `endpoints.py`'s `ENDPOINTS` list. (`/health` is checked directly
  by `_check_pixel_index_health` rather than through `_pixel_index_get`, so
  it's hand-registered as a known exception rather than generalizing the
  walk for a call site unlikely to grow siblings.)
- [`contracts/pixel_index/lint_model_usage.py`](../contracts/pixel_index/lint_model_usage.py)
  — **new field read, not modeled.** Since floorplan's views read
  parsed responses via attribute access on the pydantic models (`entry.slug`,
  `d.author.displayName`, …) rather than raw dict `.get()`, an unmodeled or
  mistyped field is a plain mypy `attr-defined` error — no bespoke schema
  extraction needed for this half. This script runs mypy (config:
  [`contracts/pixel_index/mypy.ini`](../contracts/pixel_index/mypy.ini),
  `ignore_missing_imports` so Red — not installed for this lightweight check —
  resolves to `Any` instead of erroring) and fails CI
  only on errors that name one of the canonical Pixel Index contract models.
  Everything else is outside this focused field-drift check; the floorplan
  leg of `cogs-quality.yml` runs strict mypy across every production module.

Together: `lint_endpoints.py` guards *which endpoints* the contract knows
about, `lint_model_usage.py` guards *which fields* it knows about for each
one. Both are static and run in seconds with no network access, so they gate
every PR; the live check against staging/production
(`contract-checks.yml`) is what confirms the environment itself still
matches what's registered.

### What this is not

It's not a general OpenAPI/AsyncAPI diff against pixel-index's whole spec,
and it's not gating pixel-index's own release pipeline — pixel-index has
other consumers, and it can't know all of their contracts. This only speaks
for office-cogs. If more consumers want the same guarantee, each should own
its own contract the same way; a shared `contracts/` registry with a
Pact-style `can-i-deploy` check is the natural next step if that happens.

### When it runs

[`.github/workflows/contract-checks.yml`](../.github/workflows/contract-checks.yml)
runs the check on a schedule (every 8 hours), on `workflow_dispatch`, and on
any PR touching Python under `floorplan/` or `contracts/pixel_index/`. It runs
as a matrix over known environments:

| Environment | Base URL |
|---|---|
| production | `https://pixel-index-api.nntin.xyz` |
| staging | `https://pixel-index-api-staging.nntin.xyz` |

Add a new environment (e.g. a preview deploy) by adding a row to the
`matrix.include` list under the `verify-contract` job — no other changes needed.

#### How to read a result

- **Staging passes** → pixel-index's change is compatible with what
  office-cogs needs. Safe for pixel-index to promote staging → production,
  and/or for office-cogs to point at staging directly.
- **Staging fails** → don't promote yet. Either pixel-index's change is
  breaking (fix it there) or office-cogs' usage needs to change first
  (update the catalogue implementation and
  `floorplan/contracts/pixel_index.py` together).
- **Production fails** (e.g. after a promotion, or the scheduled run catches
  something) → office-cogs is currently pointed at a broken contract; treat
  as an incident, not routine drift.

A Discord notification fires on failure via the same webhook the cog test
workflow uses, tagged with which environment broke.

### Updating the contract

Whenever the catalogue integration starts reading a new field, stops using
one, or changes how it calls an endpoint:

1. Update `floorplan/contracts/pixel_index.py` to match — this is also what validates
   responses at runtime, so it should already reflect reality.
2. If a new endpoint is called, or params/chaining change, update
   `contracts/pixel_index/endpoints.py` too.
3. Don't touch `contract.yaml` — it regenerates from the two files above the
   next time `verify.py` (or `generate_contract.py`) runs.

## Pixel Agents

Unlike Pixel Index, pixelagents doesn't call a hosted Pixel Agents API.
`pixelagents/infrastructure/webview_build.py` clones
[pixel-agents-hq/pixel-agents](https://github.com/pixel-agents-hq/pixel-agents)
at the commit pinned in `pixelagents/infrastructure/webview_vendor.commit`,
builds its webview with `npm`/`vite`, and serves the result — a build-time
source dependency rather than a runtime HTTP one (see
[red-downloader-submodules.md](red-downloader-submodules.md) for why it's
built this way instead of shipped as a submodule). The "contract" here is
therefore a different kind of shape: specific paths
(`core/src/assets/build.ts`, `core/src/assets/loader.ts`), specific exported
function names (`decodeAllCharacters`, `buildFurnitureCatalog`, …), the
`webview-ui` workspace/output layout, and the shape of
`furniture-catalog.json`/`asset-index.json` — all things upstream can rename
or restructure exactly the way Pixel Index can reshape a response.

### The model

Same principle as Pixel Index — the contract is generated from, and verified
through, the same production code path that actually depends on upstream —
just applied to a build instead of an HTTP call:

- [`contracts/pixel_agents/verify.py`](../contracts/pixel_agents/verify.py)
  — calls `pixelagents.infrastructure.webview_build.ensure_webview_built()`
  for real (a real `git clone`, `npm ci`, and `vite build` against the pinned
  commit) — not a reimplementation of it — into a scratch directory, then
  runs the same checks a working office actually needs via floorplan's
  `WebviewAssetProvider` (pixelagents builds it, floorplan serves it — see
  both cogs' Architecture.md for that split): every sprite family decodes
  (`characters`/`floors`/`walls`/`carpets`/`furniture`, plus the furniture
  catalog), a default layout is available, and every asset the built
  `index.html` references resolves on disk. A build failure reports one
  failing `build` check and three `skipped` checks, mirroring how Pixel
  Index's checker skips endpoints it can't reach.
- [`contracts/pixel_agents/generate_status_site.py`](../contracts/pixel_agents/generate_status_site.py)
  — publishes the result in the same shape as Pixel Index's site, nested
  under `pixel-agents/` on the shared status site.

### Why one environment, not two

Pixel Index has production and staging base URLs to check independently.
Pixel Agents has one pin, and
[`.github/workflows/vendor-update.yml`](../.github/workflows/vendor-update.yml)
already owns catching *upcoming* upstream drift: daily it resolves upstream
HEAD, bumps the pin on a branch, and gates that bump with this exact
clone-build-serve path
(`pixelagents/tests/test_webview_build.py::TestRealWebviewBuild`, behind
`PIXELAGENTS_REAL_WEBVIEW_BUILD=1`) plus the full pixelagents and floorplan
suites before opening a PR. `contract-checks.yml`'s Pixel Agents job runs on that PR too
(its `pull_request.paths` cover `webview_vendor.commit`), so the PR that
bumps the pin also gets an independent, standardized-shape opinion — on top
of `vendor-update.yml`'s own bespoke gate — the same way any other PR
touching the build pipeline does. There is therefore only one contract
environment, `production`: whichever commit `webview_vendor.commit` names at
the checked-out ref. On schedule/push it re-verifies the commit that's
currently shipped — something `vendor-update.yml`, which only gates at bump
time, doesn't do afterward (e.g. if upstream force-pushes over that commit,
or a runner's Node/npm version changes underneath it).

### Why one workflow

GitHub Pages serves one deployment per repository, and
`actions/deploy-pages` replaces the whole site on every run. Running Pixel
Index's and Pixel Agents' checks as two independently-scheduled workflows
would mean whichever deploys last silently erases the other's latest
results. `contract-checks.yml` runs both `verify-*` jobs and builds one
combined site in a single `build-status-site` job instead — the tradeoff is
that a PR touching only one side's paths also re-runs the other's checks
(cheap for Pixel Index's plain HTTP calls; a real `npm`/`vite` build, so a
few minutes, for Pixel Agents).

### Reading a result

- **Pass** → the pinned commit still builds, decodes every sprite family,
  and serves a resolvable bundle. Safe to keep shipping.
- **Fail** → either upstream changed something pixelagents depends on
  (fix `webview_build.py`/`webview_build_scripts/emit_decoded_assets.ts` to
  match, or coordinate with upstream) or the pin itself is bad and needs
  rolling back. Treat a failure on the currently-shipped pin (not a
  `vendor-update.yml` PR) as an incident.

A Discord notification fires on failure via the same webhook Pixel Index's
check uses.

### Status site

Both contracts' results are published together — Pixel Index at the site
root (unchanged from before this section existed, so existing links/badges
keep working), Pixel Agents nested under `/pixel-agents/`:

| Resource | URL |
|---|---|
| Pixel Index — complete status | `https://nntin.xyz/office-cogs/api/v1/status.json` |
| Pixel Agents — complete status | `https://nntin.xyz/office-cogs/pixel-agents/api/v1/status.json` |
| Pixel Agents — production | `https://nntin.xyz/office-cogs/pixel-agents/api/v1/environments/production.json` |
| Pixel Agents — overall badge | `https://nntin.xyz/office-cogs/pixel-agents/api/v1/badges/overall.json` |

Same freshness rules as Pixel Index's site: each snapshot names the checked
branch and commit, links to the workflow run, and is only trustworthy until
its `valid_until`.
