# Repo overview for agents

This repo (`pixel-agents-cogs`) is a [Red-DiscordBot](https://github.com/Cog-Creators/Red-DiscordBot)
cog repository for Pixel Agents: a set of installable `[p]cog install`
packages ("cogs") plus one CI-only shared library, developed and tested
together in one place.

## Packages

| Package | Owns | README |
|---|---|---|
| [`corridor`](../corridor) | Shared per-guild permissions, reply-style formatting, the one LLM connection pico and architect both read (`[p]corridor llm ...`), and the one shared A2A listener every registered agent is mounted on (`[p]corridor a2a ...`). Every other cog depends on it instead of reinventing any of that. | [corridor/README.md](../corridor/README.md) |
| [`pixelagents`](../pixelagents) | Vendors and builds the Pixel Agents webview (clone + `npm`/`vite`) for other cogs to serve. No runtime/Discord surface of its own, but does own the Semantic IR domain model, Pixel Agents JSON codec, color palette, and the one Config-backed store for the office layout `architect` and `painter` share (`docs/painter-design.md` part A) — a deliberate exception to "owns nothing runtime-facing," not an oversight. | [pixelagents/README.md](../pixelagents/README.md) |
| [`floorplan`](../floorplan) | Serves the built webview as a Red Dashboard page, mirrors Discord presence into it, and browses the Pixel Index layout catalogue. | [floorplan/README.md](../floorplan/README.md) |
| [`toolbox`](../toolbox) | Bot-owner Node.js/npm installation on the host, plus a Components v2 panel (`[p]toolbox tools`) for turning any `[p]help`-listed command into an LLM tool corridor's registry offers to pico. | [toolbox/README.md](../toolbox/README.md) |
| [`pico`](../pico) | An LLM-backed Discord presence: decides whether to react to a message, then acts only via a bounded tool-calling loop (never a raw LLM text send). The sole A2A coordinator -- delegates a sub-task to whichever agents (`architect`, or any future one) are currently registered in corridor's agent directory, via one `consult_<agent_key>` tool per entry. | [pico/README.md](../pico/README.md) |
| [`architect`](../architect) | A second, independent LLM agent -- never Discord-user-facing -- reachable only over the [A2A protocol](https://a2a-protocol.org/) on corridor's own shared listener (`docs/agent-directory-design.md`). Shares corridor's LLM connection with pico. Serves pixelagents' built webview bundle under its own Dashboard route (`/third-party/architect`), and edits that layout through a Semantic IR (`docs/architect-semantic-ir-design.md`) via LLM tools and `[p]architect office ...` commands. | [architect/README.md](../architect/README.md) |
| [`painter`](../painter) | A third, independent LLM agent -- never Discord-user-facing -- reachable only over A2A on corridor's own shared listener, invoked by pico exactly like `architect`. Shares one office layout with `architect`: architect knows what tiles/walls/furniture exist and where but is colorblind; painter reads/writes color only (floor tiles, walls, furniture), and can never add, remove, move, or restructure anything. See `docs/painter-design.md`. | [painter/README.md](../painter/README.md) |
| [`suggestionbox`](../suggestionbox) | Runs its own MCP tools server (`report_error`/`suggest_improvement`) that posts to a bot-owner-configured Discord channel. Registers into corridor's `AgentToolServerRegistry` so a registered A2A agent's own tool loop (`architect`, `painter` today) can call the same tools, gated per agent by a Components v2 toggle panel (`[p]suggestionbox agents`). See `docs/suggestionbox-design.md`. | [suggestionbox/README.md](../suggestionbox/README.md) |
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
python -m pytest -q architect/
python -m pytest -q painter/
python -m pytest -q suggestionbox/
python -m pytest -q testbench/
python -m pytest -q deskutils/

python -m ruff format --check corridor floorplan pixelagents toolbox pico architect painter suggestionbox testbench deskutils
python -m ruff check corridor floorplan pixelagents toolbox pico architect painter suggestionbox testbench deskutils
python -m mypy corridor floorplan pixelagents toolbox pico architect painter suggestionbox testbench deskutils
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
  agent (`architect`, and any future one) registers into, and how pico
  discovers/consults them dynamically instead of a hardcoded per-agent URL.
- [`docs/suggestionbox-design.md`](suggestionbox-design.md) — `suggestionbox`'s
  MCP feedback server, corridor's `AgentToolServerRegistry` + MCP client
  bridging it into a registered A2A agent's own tool loop, and the
  ctx-less `render_channel_reply`/`send_channel_reply` primitives corridor
  gained for it.
- [`docs/painter-design.md`](painter-design.md) — `painter`, the third A2A
  agent: extracting the Semantic IR out of `architect` into `pixelagents`
  so a second agent cog can reach the same office layout, adding wall
  color to that IR, and painter's own color-only mutation surface.
