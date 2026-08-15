# Consumer-driven contract testing against Pixel Index

The Pixel Agents catalogue integration talks to the
[Pixel Index](https://github.com/pixel-agents-hq/index) API over plain HTTP —
there's no shared package between the two repos, just a configurable base URL
(`[p]pixelagents index set`). That's deliberate:
office-cogs shouldn't hard-depend on pixel-index's code, but it does
hard-depend on pixel-index's API *shape*. This doc explains how we catch a
breaking shape change before it breaks the bot, without needing pixel-index
to semver its API (it doesn't, and it has more consumers than just us, so it
can't gate its own releases on our needs alone).

## The model

This is consumer-driven contract testing: **office-cogs owns the contract**,
not pixel-index. And within office-cogs, the contract isn't hand-written —
it's generated from the same models the bot uses to parse responses, so it
can't drift from what the code actually depends on.

- [`pixelagents/contracts/pixel_index.py`](../pixelagents/contracts/pixel_index.py) —
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

### Why generate instead of hand-write the schema

An earlier version of this hand-wrote `contract.yaml`. It missed fields
the integration actually reads (`furniture`, `visibleCols`, `areas`, `pets`,
`seats` were absent from the first draft) simply because nobody re-read the
whole file top-to-bottom while writing the YAML by hand. Generating the
schema from the same models that parse the response at runtime means the
contract can't fall out of sync with the code the way hand-maintained
duplication can — there's exactly one description of "what we depend on,"
and both the bot and the CI check read it.

## Catching drift before it reaches contract.yaml

The generated contract only covers what's registered in `endpoints.py` and
modeled in `contracts/pixel_index.py` — it can't warn about a call site or a
field that was never added there in the first place. Two lint checks close that gap, run on
every PR that touches Python code under `pixelagents/` or anything under
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
  — **new field read, not modeled.** Since PixelAgents views read
  parsed responses via attribute access on the pydantic models (`entry.slug`,
  `d.author.displayName`, …) rather than raw dict `.get()`, an unmodeled or
  mistyped field is a plain mypy `attr-defined` error — no bespoke schema
  extraction needed for this half. This script runs mypy (config:
  [`contracts/pixel_index/mypy.ini`](../contracts/pixel_index/mypy.ini),
  `ignore_missing_imports` so Red — not installed for this lightweight check —
  resolves to `Any` instead of erroring) and fails CI
  only on errors that name one of the canonical Pixel Index contract models.
  Everything else is outside this focused field-drift check; the separate
  PixelAgents quality workflow runs strict mypy across every production
  module.

Together: `lint_endpoints.py` guards *which endpoints* the contract knows
about, `lint_model_usage.py` guards *which fields* it knows about for each
one. Both are static and run in seconds with no network access, so they gate
every PR; the live check against staging/production
(`pixel-index-contract.yml`) is what confirms the environment itself still
matches what's registered.

## What this is not

It's not a general OpenAPI/AsyncAPI diff against pixel-index's whole spec,
and it's not gating pixel-index's own release pipeline — pixel-index has
other consumers, and it can't know all of their contracts. This only speaks
for office-cogs. If more consumers want the same guarantee, each should own
its own contract the same way; a shared `contracts/` registry with a
Pact-style `can-i-deploy` check is the natural next step if that happens, but
isn't needed for one consumer.

## When it runs

[`.github/workflows/pixel-index-contract.yml`](../.github/workflows/pixel-index-contract.yml)
runs the check on a schedule (every 8 hours), on `workflow_dispatch`, and on
any PR touching Python under `pixelagents/` or `contracts/pixel_index/`. It runs
as a matrix over known environments:

| Environment | Base URL |
|---|---|
| production | `https://pixel-index-api.nntin.xyz` |
| staging | `https://pixel-index-api-staging.nntin.xyz` |

Add a new environment (e.g. a preview deploy) by adding a row to the
`matrix.include` list in the workflow — no other changes needed.

### How to read a result

- **Staging passes** → pixel-index's change is compatible with what
  office-cogs needs. Safe for pixel-index to promote staging → production,
  and/or for office-cogs to point at staging directly.
- **Staging fails** → don't promote yet. Either pixel-index's change is
  breaking (fix it there) or office-cogs' usage needs to change first
  (update the catalogue implementation and
  `pixelagents/contracts/pixel_index.py` together).
- **Production fails** (e.g. after a promotion, or the scheduled run catches
  something) → office-cogs is currently pointed at a broken contract; treat
  as an incident, not routine drift.

A Discord notification fires on failure via the same webhook the cog test
workflow uses, tagged with which environment broke.

### Current status page and API

Completed checks of the repository's current default branch publish an atomic
snapshot to [the Pixel Index contract status page](https://nntin.xyz/office-cogs/).
Pull requests and runs from other branches never publish. The site is deployed
directly from a GitHub Pages artifact, so it does not create or maintain a
`gh-pages` branch or any Git history.

The human-readable page, complete API document, per-environment documents, and
badge documents are all generated from the same production/staging result
files by
[`contracts/pixel_index/generate_status_site.py`](../contracts/pixel_index/generate_status_site.py).
The stable endpoints are:

| Resource | URL |
|---|---|
| Complete status | `https://nntin.xyz/office-cogs/api/v1/status.json` |
| Production | `https://nntin.xyz/office-cogs/api/v1/environments/production.json` |
| Staging | `https://nntin.xyz/office-cogs/api/v1/environments/staging.json` |
| Overall badge | `https://nntin.xyz/office-cogs/api/v1/badges/overall.json` |
| Production badge | `https://nntin.xyz/office-cogs/api/v1/badges/production.json` |
| Staging badge | `https://nntin.xyz/office-cogs/api/v1/badges/staging.json` |

The badge documents implement Shields.io's endpoint schema and can be used as,
for example:

```markdown
![Pixel Index production](https://img.shields.io/endpoint?url=https%3A%2F%2Fnntin.xyz%2Foffice-cogs%2Fapi%2Fv1%2Fbadges%2Fproduction.json)
```

Each snapshot names the checked branch and commit, links to the workflow run,
and includes `generated_at` and `valid_until`. The page warns after the
12-hour validity window passes. If checkout, Python setup, dependency
installation, or the verifier itself fails, the environment is published as
`unknown`, never as compatible. A total GitHub Actions or Pages outage cannot
publish a replacement, so API consumers should also enforce `valid_until`.

## Updating the contract

Whenever the catalogue integration starts reading a new field, stops using
one, or changes how it calls an endpoint:

1. Update `pixelagents/contracts/pixel_index.py` to match — this is also what validates
   responses at runtime, so it should already reflect reality.
2. If a new endpoint is called, or params/chaining change, update
   `contracts/pixel_index/endpoints.py` too.
3. Don't touch `contract.yaml` — it regenerates from the two files above the
   next time `verify.py` (or `generate_contract.py`) runs.
