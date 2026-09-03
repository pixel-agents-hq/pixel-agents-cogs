# Repo overview for agents

This repo (`pixel-agents-cogs`) is a [Red-DiscordBot](https://github.com/Cog-Creators/Red-DiscordBot)
cog repository for Pixel Agents: a set of installable `[p]cog install`
packages ("cogs") plus one CI-only shared library, developed and tested
together in one place.

## Packages

| Package | Owns | README |
|---|---|---|
| [`corridor`](../corridor) | Shared permissions/replies, LLM and A2A infrastructure, agent/tool registries, event delivery, and opaque persistence for the two revisioned office aggregates. | [corridor/README.md](../corridor/README.md) |
| [`pixelagents`](../pixelagents) | Builds the Pixel Agents webview and owns the layout/seat schema, Semantic IR/codec, validation, lazy initialization, and typed facade over Corridor's opaque office state. It owns no browser transport. | [pixelagents/README.md](../pixelagents/README.md) |
| [`cctv`](../cctv) | Owns both Pixel Agents Dashboard pages, one two-route WebSocket listener, Discord/registered-agent projection, browser authorization, display settings, and per-page live pipelines. | [cctv/README.md](../cctv/README.md) |
| [`floorplan`](../floorplan) | Owns Pixel Index API/Web configuration, catalogue browsing, and loading selected layouts into the Discord aggregate. | [floorplan/README.md](../floorplan/README.md) |
| [`toolbox`](../toolbox) | Bot-owner Node.js/npm installation on the host, plus a Components v2 panel (`[p]toolbox tools`) for turning any `[p]help`-listed command into an LLM tool corridor's registry offers to pico. | [toolbox/README.md](../toolbox/README.md) |
| [`pico`](../pico) | An LLM-backed Discord presence: decides whether to react to a message, then acts only via a bounded tool-calling loop (never a raw LLM text send). The sole A2A coordinator -- delegates a sub-task to whichever agents (`architect`, `painter`, or any future one) are currently registered in corridor's agent directory, via one `consult_<agent_key>` tool per entry. | [pico/README.md](../pico/README.md) |
| [`architect`](../architect) | A2A-only LLM agent registered on Corridor's shared listener. Performs structural mutations against Pixelagents' revisioned editor aggregate and owns no browser transport. | [architect/README.md](../architect/README.md) |
| [`painter`](../painter) | A2A-only color agent using the same editor aggregate as Architect. Its surface cannot add, remove, move, or resize structure and has no direct browser notification hook. | [painter/README.md](../painter/README.md) |
| [`suggestionbox`](../suggestionbox) | Runs its own MCP tools server (`report_error`/`suggest_improvement`) that posts to a bot-owner-configured Discord channel. Registers into corridor's `AgentToolServerRegistry` so a registered A2A agent's own tool loop (`architect`, `painter` today) can call the same tools, gated per agent by a Components v2 toggle panel (`[p]suggestionbox agents`). See `docs/suggestionbox-design.md`. | [suggestionbox/README.md](../suggestionbox/README.md) |
| [`telephonepole`](../telephonepole) | Lets a bot owner register/unregister third-party MCP servers at runtime (`[p]telephonepole add/remove/list`), registering each into corridor's `AgentToolServerRegistry` so a registered A2A agent's own tool loop can call their tools, gated per server and per agent by a Components v2 toggle panel (`[p]telephonepole agents <name>`). Generalizes `suggestionbox`'s self-registration of its own in-process server to any external MCP endpoint. See `docs/telephonepole-design.md`. | [telephonepole/README.md](../telephonepole/README.md) |
| [`testbench`](../testbench) | Bot-owner-only: publishes Corridor agent events through a generated Discord UI for exercising CCTV projection without a real gateway event. | [testbench/README.md](../testbench/README.md) |
| [`deskutils`](../deskutils) | Small Discord utilities with no state of their own; today just `[p]deskutils time`, showing the current time via Discord's native per-viewer timestamp markup plus explicit UTC/named-zone formatting. | [deskutils/README.md](../deskutils/README.md) |
| [`contracts`](../contracts) | **Not a cog** — `"type": "SHARED_LIBRARY"` in its `info.json`, so Red's Downloader skips it. CI-only: consumer-driven contract tests against Pixel Index and Pixel Agents, plus the reply-channel lint. (It does have a no-op `setup()` — purely to stop dev-time hot reload tooling from reporting a spurious failure; see `contracts/__init__.py`.) | [contracts/README.md](../contracts/README.md) |

The current office boundary is: Corridor persists, Pixelagents validates, CCTV
hosts/projects, Floorplan consumes Pixel Index, and Architect/Painter mutate the
editor aggregate. See [`docs/cctv-design.md`](cctv-design.md).

See [`docs/architecture.md`](architecture.md) for Mermaid diagrams of how
these packages depend on and relate to each other — the dependency graph,
an ownership map, office-state/CCTV-browser/agent-event runtime data flow,
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
  Every other cog declares it in `required_cogs` and auto-loads it. Most
  cogs do this via their own local `dependency_loader.ensure_corridor_loaded()`
  wrapper; `cctv` is the one exception, calling
  `corridor.dependency_loader.ensure_loaded(bot, "corridor", "Corridor")`
  directly with no local wrapper of its own (a known fragility — see
  [`docs/dependency-loading.md`](dependency-loading.md)). Permission checks go
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
  erroring.
- **`corridor/ui_limits.py`** is a pure, framework-agnostic checker for
  Discord's undocumented-at-runtime component limits (modal title length,
  button label length, etc.), imported as `from corridor import ui_limits`
  by UI test suites across multiple cogs (`corridor`, `floorplan`,
  `toolbox`, `suggestionbox`, `testbench`). It has no `discord`/`redbot`
  import of its own — keep it that way if you touch it.

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
python -m pytest -q cctv/tests
python -m pytest -q floorplan/tests
python -m pytest -q pixelagents/tests
python -m pytest -q toolbox/
python -m pytest -q pico/
python -m pytest -q architect/
python -m pytest -q painter/
python -m pytest -q suggestionbox/
python -m pytest -q telephonepole/
python -m pytest -q testbench/
python -m pytest -q deskutils/

python -m ruff format --check corridor cctv floorplan pixelagents toolbox pico architect painter suggestionbox telephonepole testbench deskutils
python -m ruff check corridor cctv floorplan pixelagents toolbox pico architect painter suggestionbox telephonepole testbench deskutils
python -m mypy corridor cctv floorplan pixelagents toolbox pico architect painter suggestionbox telephonepole testbench deskutils
python -m unittest discover -s contracts/tests
python -m contracts.discord_replies.lint_reply_channel
```

`corridor`'s suite binds a real loopback A2A listener during its own
tests (and pico's `test_architect_client.py` does the same, mounting a
real architect executor on it to exercise a live pico→architect round
trip) -- these aren't network-mocked, so a sandboxed environment needs
loopback binding allowed for `127.0.0.1`.

`floorplan`'s suite additionally needs `pixel_index` contract dependencies
(`pip install -r contracts/pixel_index/requirements.txt`) for the Pixel
Index lint/verify steps — see [`contracts/README.md`](../contracts/README.md).

## Further reading

- [`docs/architecture.md`](architecture.md) — cross-cog dependency graph,
  ownership map, and runtime/CI data-flow diagrams.
- [`docs/dependency-loading.md`](dependency-loading.md) — how cross-cog
  dependencies get loaded, why corridor's bootstrap is duplicated per cog,
  and when to use `ensure_loaded` vs `ensure_importable`.
- [`docs/dependency-cascades.md`](dependency-cascades.md) — what happens to
  an already-loaded dependency reference when corridor or pixelagents
  reload independently of the cog holding it: corridor cascades an unload,
  pixelagents pushes a fresh reference instead.
- [`docs/corridor.md`](corridor.md) — corridor's permission model in full.
- [`docs/contract-testing.md`](contract-testing.md) — why/how Pixel Index
  and Pixel Agents contracts are generated and verified in CI.
- [`docs/red-downloader-submodules.md`](red-downloader-submodules.md) — what
  Red's Downloader does and doesn't do with git submodules, and why that
  ruled out a submodule-based vendoring approach.
- [`docs/architect-semantic-ir-design.md`](architect-semantic-ir-design.md) —
  the Semantic IR between `architect`'s LLM tools/Discord commands and
  Pixel Agents' raw layout JSON, the generated furniture-style manifest,
  and the mutation/validation service both callers share.
- [`docs/agent-directory-design.md`](agent-directory-design.md) — corridor
  as the one shared A2A listener + agent directory every A2A-reachable
  agent (`architect`, `painter`, and any future one) registers into, and
  how pico discovers/consults them dynamically instead of a hardcoded
  per-agent URL.
- [`docs/suggestionbox-design.md`](suggestionbox-design.md) — `suggestionbox`'s
  MCP feedback server, corridor's `AgentToolServerRegistry` + MCP client
  bridging it into a registered A2A agent's own tool loop, and the
  ctx-less `render_channel_reply`/`send_channel_reply` primitives corridor
  gained for it.
- [`docs/painter-design.md`](painter-design.md) — `painter`, the third A2A
  agent: extracting the Semantic IR out of `architect` into `pixelagents`
  so a second agent cog can reach the same office layout, adding wall
  color to that IR, and painter's own color-only mutation surface.
- [`docs/telephonepole-design.md`](telephonepole-design.md) — `telephonepole`,
  generalizing `suggestionbox`'s self-registration of its own in-process
  MCP server into a bot-owner-managed set of third-party MCP servers,
  registered/unregistered at runtime and gated per server and per agent.
