# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## `.claude/settings.json` may show local, unrelated diffs

Third-party tooling (e.g. an agent-orchestrator harness) can rewrite
`.claude/settings.json` on its own, outside any task you were given —
typically adding/removing session-tracking hooks unrelated to your work.
If `git status`/`git diff` shows a `.claude/settings.json` change you
didn't make, do not stage or commit it as part of your change; leave it
out of your `git add` and out of the commit unless the user explicitly
asks you to modify that file.

## What this repo is

`pixel-agents-cogs` is a [Red-DiscordBot](https://github.com/Cog-Creators/Red-DiscordBot)
cog repository: independently installable `[p]cog install` packages ("cogs")
plus one CI-only shared library (`contracts/`), developed together. Read
[`docs/AGENTS.md`](../docs/AGENTS.md) for a one-paragraph purpose of each
package and [`docs/architecture.md`](../docs/architecture.md) for the
cross-cog dependency graph and runtime data-flow diagrams — both are worth
reading in full before making a cross-cog change; this file only covers
what changes how you should act.

Packages: `corridor` (shared permissions/reply-rendering/LLM
connection/A2A listener — hidden but load-bearing), `pixelagents` (vendors
+ builds the webview), `floorplan` (serves it, mirrors Discord presence),
`toolbox` (host Node.js install + LLM tool toggle panel), `pico` (LLM
Discord presence, sole A2A coordinator), `architect` (second LLM agent,
A2A-only), `testbench` (owner-only bus-event publisher for testing),
`deskutils` (small utilities), `contracts` (CI-only, not a runtime cog).

## Commands

Tests **must** be run one cog at a time — each cog's `conftest.py`
installs its own, mutually incompatible `sys.modules["redbot.core"]`
stub, so collecting two cogs in one `pytest` process makes whichever
loads first silently win, producing a wall of unrelated-looking failures.
A `PreToolUse` hook (`.claude/hooks/check_pytest_scope.py`) blocks any
`pytest` invocation spanning multiple cogs or with no test-path argument
at all — if you hit that block, split the command instead of trying to
work around it.

```sh
python -m pytest -q corridor/
python -m pytest -q floorplan/tests
python -m pytest -q pixelagents/tests
python -m pytest -q toolbox/
python -m pytest -q pico/
python -m pytest -q architect/
python -m pytest -q testbench/
python -m pytest -q deskutils/

# lint/format/types run fine across all cogs at once:
python -m ruff format --check corridor floorplan pixelagents toolbox pico architect testbench deskutils
python -m ruff check corridor floorplan pixelagents toolbox pico architect testbench deskutils
python -m mypy corridor floorplan pixelagents toolbox pico architect testbench deskutils

# CI-only contract/lint checks:
python -m unittest discover -s contracts/tests
python -m contracts.discord_replies.lint_reply_channel
```

A single test: `python -m pytest -q corridor/tests/test_reply_sender.py::TestReplySender::test_foo`.

`corridor`'s suite (and pico's `test_architect_client.py`) binds a real
loopback A2A listener — not network-mocked — so a sandboxed environment
needs `127.0.0.1` loopback binding allowed. `floorplan`'s suite additionally
needs `pip install -r contracts/pixel_index/requirements.txt`.

See [`.github/workflows/cogs-quality.yml`](../.github/workflows/cogs-quality.yml)
for the exact per-cog dependency matrix if a test run fails on a missing
package.

## Scaffolding a new cog

Never hand-write a new cog's skeleton. Generate it from
[`.cookiecutter/cog-cookiecutter`](../.cookiecutter/cog-cookiecutter):

```sh
cookiecutter .cookiecutter/cog-cookiecutter
```

This produces the standard `domain/` / `application/` / `infrastructure/`
/ `adapters/` layering (see below), a `dependency_loader.py` with
corridor's bootstrap already wired, a rolled `Config` identifier (filled
in by a post-gen hook — never invent one by hand), and a starter test
suite/conftest. `cog_name` must be lowercase snake_case (enforced by a
pre-gen hook).

## Internal layering (every cog)

Every cog — and the cookiecutter template — follows the same layout:
`domain/` (pure logic, no discord/redbot imports), `application/`
(use-case services), `infrastructure/` (Config/filesystem/network
adapters), `adapters/` (discord.py commands, views, cog composition).
Full rationale in [`pixelagents/Architecture.md`](../pixelagents/Architecture.md);
each package's own `Architecture.md` (where present) covers its specifics.

## Rules that will bite you if ignored

- **corridor is load-bearing infrastructure, not optional.** Every cog
  declares it in `required_cogs` and auto-loads it via
  `dependency_loader.ensure_corridor_loaded()`. All permission checks go
  through `corridor.require_permission(ctx, group_key)` /
  `corridor.capabilities_satisfy(member, group_key)`; **all replies go
  through `corridor.send_reply(...)` / `render_reply(...)`, never a raw
  `ctx.send`/`interaction.response.send_message`/`.followup.send`.**
  `contracts/discord_replies/lint_reply_channel.py` AST-scans every
  command handler's call graph in CI and fails the build on any raw send
  that doesn't reach corridor's renderer. See [`docs/corridor.md`](../docs/corridor.md)
  for the full permission-group model.
- **`required_cogs` does not auto-load anything** — Red never reads it;
  it's a Downloader install hint only. Every cross-cog dependency (corridor
  included) is hand-loaded via `corridor.dependency_loader.ensure_loaded`/
  `ensure_importable`, called from `setup()`/`cog_load()`. See
  [`docs/dependency-loading.md`](../docs/dependency-loading.md).
- **Never put a module-scope `from corridor... import X` (or any other
  cross-cog import) at the top of a heavily-imported file** (e.g.
  `adapters/cog_base.py`) unless it's `TYPE_CHECKING`-only. Red re-execs
  every cached module on each load/reload attempt, including moments the
  dependency isn't loaded yet — a bare top-level import crashes with
  `ModuleNotFoundError` before `setup()` runs. Put runtime-only cross-cog
  imports inside the function body that needs them instead. This already
  broke production once (`docs/dependency-loading.md`, `pixelagents/__init__.py`
  docstring has the trace).
- **Two trees, one writable.** The installed package tree is read-only at
  runtime — never write into a cog's own source directory. Anything a cog
  writes (config, build output, downloaded binaries) goes under
  `redbot.core.data_manager.cog_data_path(self)`. See
  [`docs/red-downloader-submodules.md`](../docs/red-downloader-submodules.md).
- **`info.json` keys are case-sensitive** and matched exactly against
  Red's schema — an uppercase key silently no-ops rather than erroring.
- **`corridor/ui_limits.py` has no discord/redbot import and must stay
  that way** — it's a pure Discord-component-limit checker imported by
  both corridor's and floorplan's UI tests.
- **Contract files under `contracts/` are generated, not hand-written**
  (from the same models/dataclasses the runtime code uses) — some are
  gitignored build artifacts (`pixel_index/contract.yaml`), others are
  committed and CI-diffed (`pixel-agents-consumer-contract.yaml`,
  `corridor/corridor.yaml`). Never hand-edit a generated one; update the
  source model and regenerate. See [`docs/contract-testing.md`](../docs/contract-testing.md).
- If you fix a piece of documented drift while building or touching
  contract tests, fix it in the same PR rather than only flagging it.
