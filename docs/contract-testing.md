# Consumer-driven contract testing against Pixel Index

`pixelagents.py` talks to the [Pixel Index](https://github.com/pixel-agents-hq/index)
API over plain HTTP — there's no shared package between the two repos, just a
configurable base URL (`[p]pixelagents pixelindex set`). That's deliberate:
office-cogs shouldn't hard-depend on pixel-index's code, but it does
hard-depend on pixel-index's API *shape*. This doc explains how we catch a
breaking shape change before it breaks the bot, without needing pixel-index
to semver its API (it doesn't, and it has more consumers than just us, so it
can't gate its own releases on our needs alone).

## The model

This is consumer-driven contract testing: **office-cogs owns the contract**,
not pixel-index.

- [`contracts/pixel-index/contract.yaml`](../contracts/pixel-index/contract.yaml)
  describes only the endpoints, fields, and types `pixelagents.py` actually
  reads — not pixel-index's full API. It's hand-curated, not generated from
  pixel-index's OpenAPI spec, on purpose: if we diffed the full spec, every
  unrelated endpoint or field pixel-index adds would show up as "drift" even
  though we never touch it. Scoping the contract to only what we consume
  means a check failure always means something office-cogs actually cares
  about changed.
- [`contracts/pixel-index/verify.py`](../contracts/pixel-index/verify.py)
  takes the contract and a target base URL, calls each endpoint for real, and
  validates the live response against the contract's JSON Schema fragments.
  It also chains requests where needed — e.g. `list_layouts` derives a real
  `slug` from its response so `layout_detail` can be checked against actual
  data instead of a hardcoded slug that might not exist in a given
  environment.
- A pass means "this environment is safe for office-cogs to consume right
  now." A fail means something office-cogs reads has changed shape, and the
  bot should not be pointed at that environment until the contract (and the
  corresponding code in `pixelagents.py`) is reconciled with reality.

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
any PR touching the contract itself. It runs as a matrix over known
environments:

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
  (update `pixelagents.py` and the contract together).
- **Production fails** (e.g. after a promotion, or the scheduled run catches
  something) → office-cogs is currently pointed at a broken contract; treat
  as an incident, not routine drift.

A Discord notification fires on failure via the same webhook the cog test
workflow uses, tagged with which environment broke.

## Updating the contract

Whenever `pixelagents.py` starts reading a new field, stops using one, or
changes how it calls an endpoint, update `contract.yaml` in the same PR. The
contract and the code it protects should never drift from each other — the
contract is a description of the code's assumptions, not an independent
spec.
