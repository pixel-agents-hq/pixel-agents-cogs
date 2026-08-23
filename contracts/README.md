# contracts

Consumer-driven contract testing — verifies this repo's assumptions about
external services (Pixel Index, Pixel Agents) against their real
environments. **Not a runtime-loaded cog.**

`contracts` is a separate top-level package that exists purely for CI. Its
`info.json` declares `"type": "SHARED_LIBRARY"` specifically so Red's
Downloader excludes it from cog discovery — without that marker, Red would
offer it as an installable cog. `contracts/__init__.py` does define a
no-op `setup()`, but not because it's a real cog: it's there only so
dev-time hot reload tooling (which infers "reloadable cog" from any
top-level package with an `info.json`, without checking `type`/`hidden`)
doesn't report a spurious reload failure — see that function's docstring.

It owns the contract for two external dependencies neither `pixelagents`
nor `floorplan` controls, plus one internal one, corridor's own Pub/Sub
event bus:

- **[Pixel Index](https://github.com/pixel-agents-hq/index)** — a plain
  HTTP API. The contract is generated from the same pydantic models
  (`floorplan/contracts/pixel_index.py`) that validate real responses at
  runtime, so it can't drift from what the code actually depends on.
- **[Pixel Agents](https://github.com/pixel-agents-hq/pixel-agents)** — a
  build-time source dependency (`pixelagents` clones and builds its webview
  at the commit pinned in `webview_vendor.commit`, and `floorplan` serves
  the result). The "contract" here is a real clone + `npm ci` + `vite
  build`, checked against the same asset decoding the office actually
  needs. A second, narrower contract
  (`pixel_agents/pixel-agents-consumer-contract.yaml`) covers just the
  subset of that same repo's WebSocket message vocabulary
  `pixelagents.contracts.outbound` actually builds — see below for how it
  differs from Pixel Index's `contract.yaml`.
- **corridor's Pub/Sub domain model** — internal, not external (see
  [`docs/corridor-pubsub-design.md`](../docs/corridor-pubsub-design.md)).
  `corridor/corridor.yaml` is **generated, but committed**, produced by
  `corridor/event_catalog.py::build_contract()` introspecting
  `corridor/domain/models.py`'s real dataclasses — the same generate-and-commit
  pattern as `pixel-agents-consumer-contract.yaml` below. It lives inside
  `corridor/`, not `contracts/`, because a real cog (`testbench`) needs
  this same schema at runtime to build its UI, and `contracts/` is
  documented (`docs/corridor.md`) as CI-only, never imported by a loaded
  cog — `contracts/corridor/generate_corridor_contract.py` just imports
  `build_contract()` from `corridor` rather than owning the introspection
  itself. CI's `generate_corridor_contract.py --check` fails on any diff
  from the committed copy; `lint_corridor_contract.py` keeps one narrower
  job on top, checking every declared event name is still mentioned in
  the design doc's own text.

Pixel Index and Pixel Agents (the build-pipeline contract) are checked live
on a schedule and on relevant PRs, and published to a shared status site so
a break is visible before it reaches a bot host.

## `pixel-agents-consumer-contract.yaml`: generated, but committed

Unlike `pixel_index/contract.yaml` (gitignored, regenerated fresh on every
`verify.py` run — never a reviewable diff), `pixel_agents/pixel-agents-consumer-contract.yaml`
**is committed**. It's produced by `generate_consumer_contract.py`
introspecting `pixelagents.contracts.outbound`'s `TypedDict`s (plain
`TypedDict`s, not pydantic models — `model_json_schema()` doesn't apply
here), but CI regenerates it and fails on any diff from the committed copy
instead of silently overwriting. A change to `outbound.py` always shows up
as a reviewable diff to this file in the same PR. Checked two ways:

- **Offline** (`lint_outbound_contract.py`, every PR): do
  `pixelagents.contracts.outbound`'s builders still produce exactly what
  we've committed to?
- **Live** (a new `consumer_contract_drift` check appended to
  `pixel_agents/verify_outbound.py`'s existing checks, scheduled + PR-gated,
  needs the real vendor clone): does upstream's actual, currently-pinned
  `core/asyncapi.yaml` still support every field this contract declares?

It's deliberately narrower than everything `docs/corridor-pubsub-design.md`
has verified against upstream — only what's actually built today. See that
doc's "Verifying this design: two committed contracts" section.

## Layout

| Path | Purpose |
|---|---|
| `pixel_index/endpoints.py` | Which Pixel Index endpoints get called, params, and chaining |
| `pixel_index/generate_contract.py` | Builds `contract.yaml` from `endpoints.py` + the pydantic models (gitignored build artifact, never hand-edited) |
| `pixel_index/verify.py` | Regenerates the contract, calls each endpoint for real, validates the response |
| `pixel_index/lint_endpoints.py` | CI lint: fails if a called endpoint isn't registered in `endpoints.py` |
| `pixel_index/lint_model_usage.py` | CI lint: fails if a field read on a contract model isn't modeled (via mypy) |
| `pixel_agents/verify.py` | Runs a real webview build against the pinned commit and checks the result |
| `pixel_agents/verify_outbound.py` | Captures real outbound messages and validates them against the vendor schema, our own committed contract, and (live) checks the contract itself against the vendor schema |
| `pixel_agents/generate_consumer_contract.py` | Builds `pixel-agents-consumer-contract.yaml` from `pixelagents.contracts.outbound`'s TypedDicts (**committed**, not gitignored — CI fails on drift instead of always overwriting) |
| `pixel_agents/pixel-agents-consumer-contract.yaml` | The subset of wire `ServerMessage` schemas `pixelagents.contracts.outbound` actually builds; generated, committed, reviewable |
| `pixel_agents/lint_outbound_contract.py` | CI lint: fails if a captured outbound message violates the committed consumer contract (offline) |
| `corridor/generate_corridor_contract.py` | Thin CLI wrapper: renders and `--check`s/writes `../corridor/corridor.yaml` from `corridor.event_catalog.build_contract()` (**committed**, not gitignored — CI fails on drift instead of always overwriting) |
| [`../corridor/corridor.yaml`](../corridor/corridor.yaml) | Every `Agent`-prefixed type in `corridor/domain/models.py`; generated, committed, reviewable. Lives inside `corridor/`, not here — see the bullet above |
| `corridor/lint_corridor_contract.py` | CI lint: fails if a name declared in `corridor.yaml` isn't mentioned in `docs/corridor-pubsub-design.md`'s text (doc cross-reference only — structural correctness against real code is `generate_corridor_contract.py --check`'s job) |
| `discord_replies/lint_reply_channel.py` | CI lint: fails on a raw Discord send reachable from a command handler without going through corridor's `send_reply`/`render_reply` |

## Running checks locally

```sh
python -m contracts.pixel_index.generate_contract
python -m contracts.pixel_index.verify
python -m contracts.pixel_index.lint_endpoints
python -m contracts.pixel_index.lint_model_usage
python -m unittest discover -s contracts/pixel_index/tests -v

python -m contracts.pixel_agents.generate_consumer_contract
python -m contracts.pixel_agents.lint_outbound_contract
python -m unittest discover -s contracts/pixel_agents/tests -v

python -m contracts.corridor.generate_corridor_contract
python -m contracts.corridor.lint_corridor_contract
python -m unittest discover -s contracts/corridor/tests -v
```

## Docs

See [`docs/contract-testing.md`](../docs/contract-testing.md) for the full
methodology, why the contract is generated instead of hand-written, how to
read a CI result, and the status site URLs.
