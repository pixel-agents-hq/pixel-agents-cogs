# contracts

Consumer-driven contract testing — verifies this repo's assumptions about
external services (Pixel Index, Pixel Agents) against their real
environments. **Not a runtime-loaded cog.**

`contracts` is a separate top-level package that exists purely for CI. Its
`info.json` declares `"type": "SHARED_LIBRARY"` specifically so Red's
Downloader excludes it from cog discovery — without that marker, Red would
try `bot.load_extension("contracts")` and fail since it has no `setup()`.

It owns the contract for two external dependencies neither `pixelagents`
nor `floorplan` controls:

- **[Pixel Index](https://github.com/pixel-agents-hq/index)** — a plain
  HTTP API. The contract is generated from the same pydantic models
  (`floorplan/contracts/pixel_index.py`) that validate real responses at
  runtime, so it can't drift from what the code actually depends on.
- **[Pixel Agents](https://github.com/pixel-agents-hq/pixel-agents)** — a
  build-time source dependency (`pixelagents` clones and builds its webview
  at the commit pinned in `webview_vendor.commit`, and `floorplan` serves
  the result). The "contract" here is a real clone + `npm ci` + `vite
  build`, checked against the same asset decoding the office actually
  needs.

Both are checked live on a schedule and on relevant PRs, and published to a
shared status site so a break is visible before it reaches a bot host.

## Layout

| Path | Purpose |
|---|---|
| `pixel_index/endpoints.py` | Which Pixel Index endpoints get called, params, and chaining |
| `pixel_index/generate_contract.py` | Builds `contract.yaml` from `endpoints.py` + the pydantic models (gitignored build artifact, never hand-edited) |
| `pixel_index/verify.py` | Regenerates the contract, calls each endpoint for real, validates the response |
| `pixel_index/lint_endpoints.py` | CI lint: fails if a called endpoint isn't registered in `endpoints.py` |
| `pixel_index/lint_model_usage.py` | CI lint: fails if a field read on a contract model isn't modeled (via mypy) |
| `pixel_agents/verify.py` | Runs a real webview build against the pinned commit and checks the result |
| `discord_replies/lint_reply_channel.py` | CI lint: fails on a raw Discord send reachable from a command handler without going through corridor's `send_reply`/`render_reply` |

## Running checks locally

```sh
python -m contracts.pixel_index.generate_contract
python -m contracts.pixel_index.verify
python -m contracts.pixel_index.lint_endpoints
python -m contracts.pixel_index.lint_model_usage
python -m unittest discover -s contracts/pixel_index/tests -v
```

## Docs

See [`docs/contract-testing.md`](../docs/contract-testing.md) for the full
methodology, why the contract is generated instead of hand-written, how to
read a CI result, and the status site URLs.
