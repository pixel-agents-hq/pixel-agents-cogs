# Repo overview for agents

This repo (`pixel-agents-cogs`) is a [Red-DiscordBot](https://github.com/Cog-Creators/Red-DiscordBot)
cog repository for Pixel Agents: a set of installable `[p]cog install`
packages ("cogs") plus one CI-only shared library, developed and tested
together in one place.

## Packages

| Package | Owns | README |
|---|---|---|
| [`corridor`](../corridor) | Shared per-guild permissions and reply-style formatting. Every other cog depends on it instead of reinventing either. | [corridor/README.md](../corridor/README.md) |
| [`pixelagents`](../pixelagents) | Vendors and builds the Pixel Agents webview (clone + `npm`/`vite`) for other cogs to serve. No runtime/Discord surface of its own. | [pixelagents/README.md](../pixelagents/README.md) |
| [`floorplan`](../floorplan) | Serves the built webview as a Red Dashboard page, mirrors Discord presence into it, and browses the Pixel Index layout catalogue. | [floorplan/README.md](../floorplan/README.md) |
| [`toolbox`](../toolbox) | Bot-owner Node.js/npm installation on the host, plus a Components v2 panel (`[p]toolbox tools`) for turning any `[p]help`-listed command into an LLM tool corridor's registry offers to pico. | [toolbox/README.md](../toolbox/README.md) |
| [`pico`](../pico) | An LLM-backed Discord presence: decides whether to react to a message, then acts only via a bounded tool-calling loop (never a raw LLM text send). | [pico/README.md](../pico/README.md) |
| [`testbench`](../testbench) | Bot-owner-only: publishes any corridor Pub/Sub event through a Discord UI generated from corridor's own event catalog, for exercising floorplan's canvas rendering without a real Discord presence change or message. | [testbench/README.md](../testbench/README.md) |
| [`deskutils`](../deskutils) | Small Discord utilities with no state of their own; today just `[p]deskutils time`, showing the current time via Discord's native per-viewer timestamp markup plus explicit UTC/named-zone formatting. | [deskutils/README.md](../deskutils/README.md) |
| [`contracts`](../contracts) | **Not a cog** — `"type": "SHARED_LIBRARY"` in its `info.json`, so Red's Downloader skips it. CI-only: consumer-driven contract tests against Pixel Index and Pixel Agents, plus the reply-channel lint. (It does have a no-op `setup()` — purely to stop dev-time hot reload tooling from reporting a spurious failure; see `contracts/__init__.py`.) | [contracts/README.md](../contracts/README.md) |

`pixelagents` and `floorplan` used to be one combined cog; [issue #21](https://github.com/pixel-agents-hq/pixel-agents-cogs/issues/21)
split "owns the vendor and the build" from "owns everything that consumes
the result." That split has landed on `develop` — treat both as separate,
present-day cogs, not a pending change.

See [`docs/architecture.md`](architecture.md) for Mermaid diagrams of how
these packages depend on and relate to each other — the dependency graph,
an ownership map, cross-package runtime data flow for floorplan and pico,
and the CI-only relationships `contracts/` adds on top of all of it.

## Internal layering

Every cog here (and the `.cookiecutter/cog-cookiecutter` template new cogs
are generated from) follows the same internal layout: `domain/` (pure
logic, no discord/redbot imports), `application/` (use-case services),
`infrastructure/` (Config/filesystem/network adapters), `adapters/`
(discord.py commands, views, cog composition). The fullest write-up of this
convention, including why it's split this way, is
[`pixelagents/Architecture.md`](../pixelagents/Architecture.md); each
package's own `Architecture.md` (where present) covers its specifics.

## Before making any change

- **corridor is load-bearing infrastructure, not an optional dependency.**
  Every other cog declares it in `required_cogs` and auto-loads it via
  `dependency_loader.ensure_corridor_loaded()`. Permission checks go
  through `corridor.require_permission(ctx, group_key)`; replies go through
  `corridor.send_reply(...)`, never a raw `ctx.send`/`interaction.response.send_message`.
  `contracts/discord_replies/lint_reply_channel.py` runs in CI and fails a
  build on any command handler that reaches a raw Discord send without
  going through corridor. See [`docs/corridor.md`](corridor.md) for the
  full permission model, and
  [`docs/dependency-loading.md`](dependency-loading.md) for how/why
  cross-cog dependencies (corridor included) get loaded at all — Red has
  no built-in mechanism for this.
- **Two separate trees, only one of them writable at runtime.** The
  installed package tree (what Downloader clones/copies) is read-only at
  runtime — never write into it. Anything a cog writes (config, build
  output, downloaded binaries) belongs under Red's per-cog
  `redbot.core.data_manager.cog_data_path(self)`. `pixelagents`' webview
  build is the reference example: it clones and builds into
  `cog_data_path`, never into `pixelagents/`. See
  [`docs/red-downloader-submodules.md`](red-downloader-submodules.md) for
  why.
- **`info.json` fields are matched case-sensitively by Red.** Use the
  lowercase keys from Red's `red_cog_repo.schema.json` /
  `red_cog.schema.json` — an uppercase key silently no-ops instead of
  erroring (this bit the root `info.json` once; see git history around
  "fix(repo): correct root info.json to Red's repo schema").
- **`corridor/ui_limits.py`** is a pure, framework-agnostic checker for
  Discord's undocumented-at-runtime component limits (modal title length,
  button label length, etc.), imported as `from corridor import ui_limits`
  by both `corridor`'s and `floorplan`'s UI test suites. It has no
  `discord`/`redbot` import of its own — keep it that way if you touch it.

## Local quality gate

Each cog's tests, lint, and types run independently (see
[`.github/workflows/cogs-quality.yml`](../.github/workflows/cogs-quality.yml)
for the exact per-cog matrix). Formatting, linting, and typing can be
checked across all cogs at once; **tests cannot** — each cog installs its
own, mutually incompatible `sys.modules["redbot.core"]` stub, so running
two cogs' suites in one `pytest` process makes whichever collects first
silently win for the whole run. Run each cog's tests in its own process,
from the repo root:

```sh
python -m pytest -q corridor/
python -m pytest -q floorplan/tests
python -m pytest -q pixelagents/tests
python -m pytest -q toolbox/
python -m pytest -q pico/
python -m pytest -q testbench/
python -m pytest -q deskutils/

python -m ruff format --check corridor floorplan pixelagents toolbox pico testbench deskutils
python -m ruff check corridor floorplan pixelagents toolbox pico testbench deskutils
python -m mypy corridor floorplan pixelagents toolbox pico testbench deskutils
python -m unittest discover -s contracts/tests
python -m contracts.discord_replies.lint_reply_channel
```

`floorplan`'s suite additionally needs `pixel_index` contract dependencies
(`pip install -r contracts/pixel_index/requirements.txt`) for the Pixel
Index lint/verify steps — see [`contracts/README.md`](../contracts/README.md).

## Further reading

- [`docs/architecture.md`](architecture.md) — cross-cog dependency graph,
  ownership map, and runtime/CI data-flow diagrams.
- [`docs/dependency-loading.md`](dependency-loading.md) — how cross-cog
  dependencies get loaded, why corridor's bootstrap is duplicated per cog,
  and when to use `ensure_loaded` vs `ensure_importable`.
- [`docs/corridor.md`](corridor.md) — corridor's permission model in full.
- [`docs/contract-testing.md`](contract-testing.md) — why/how Pixel Index
  and Pixel Agents contracts are generated and verified in CI.
- [`docs/red-downloader-submodules.md`](red-downloader-submodules.md) — what
  Red's Downloader does and doesn't do with git submodules, and why that
  ruled out a submodule-based vendoring approach.
