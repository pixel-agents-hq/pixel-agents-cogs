# AGENTS.md

Instructions for coding agents working in this repository. `CLAUDE.md` is a
symlink to this file.

`pixel-agents-cogs` is a [Red-DiscordBot](https://github.com/Cog-Creators/Red-DiscordBot)
cog repository: independently installable cogs plus `contracts/`, a CI-only
shared library. Read [`docs/AGENTS.md`](docs/AGENTS.md) for each package's
purpose and [`docs/architecture.md`](docs/architecture.md) for the cross-cog
dependency graph before a cross-cog change.

## Skills and hooks

- **Skills** (`.claude/skills/`):
  - `advance-vendor`: bump the vendored Pixel Agents pin
    (`pixelagents/infrastructure/webview_vendor.commit`/`.repo`), fix what it
    breaks, and take over the automated `vendor-update.yml` PR.
- **Hooks** (`.claude/hooks/`, explained in
  [`.claude/STOP_HOOKS.md`](.claude/STOP_HOOKS.md)) block known-bad Bash
  commands and print the working one:
  - `check_pytest_scope.py`: a `pytest` run spanning several cogs, or with no
    path.
  - `check_gh_pr_edit.py`: `gh pr edit`. Use
    `gh api repos/<owner>/<repo>/pulls/<n> -X PATCH` instead.
  - `check_public_text.py`: a commit, PR, issue, comment or `gh api` write
    whose text describes a credential's type, scopes or account access.
    Rephrase it; never work around the guard.

## Commands

Run tests **one cog per `pytest` process**: each cog's `conftest.py` installs
its own, incompatible `sys.modules["redbot.core"]` stub.

```sh
python -m pytest -q corridor/            # same for each cog directory
python -m pytest -q switchboard/         # needs red-discordbot installed
python -m pytest -q corridor/tests/test_reply_sender.py::TestReplySender::test_foo

COGS="corridor floorplan pixelagents cctv toolbox pico architect painter suggestionbox telephonepole bootcamp testbench deskutils animator switchboard"
python -m ruff format --check $COGS e2e
python -m ruff check $COGS e2e
python -m mypy $COGS e2e

python -m unittest discover -s contracts/tests
python -m contracts.discord_replies.lint_reply_channel
python -m contracts.info_json.lint_info_json
python -m contracts.runtime_hygiene.lint_runtime_hygiene
python -m unittest discover -s contracts/red_release/tests
python3 -m unittest discover -s .claude/hooks -p 'test_*.py'

PIXELAGENTS_REAL_WEBVIEW_BUILD=1 python -m pytest -q e2e/   # multi-cog, see e2e/README.md
```

- The `corridor`, `pico` and `cctv` suites bind real `127.0.0.1` listeners.
- `floorplan` also needs `pip install -r contracts/pixel_index/requirements.txt`.
- Per-cog dependencies are in [`.github/workflows/cogs-quality.yml`](.github/workflows/cogs-quality.yml).
- Don't run ruff format over `contracts/`; CI doesn't, and it rewrites
  unrelated files.

## Coding patterns

- **Scaffold new cogs** with `cookiecutter .cookiecutter/cog-cookiecutter`,
  never by hand. It generates the layering, `dependency_loader.py`, a `Config`
  identifier and starter tests.
- **Layering in every cog:**
  - `domain/`: pure logic, no discord/redbot imports;
  - `application/`: use cases;
  - `infrastructure/`: Config, filesystem, network;
  - `adapters/`: commands, views, cog composition.

  See [`pixelagents/Architecture.md`](pixelagents/Architecture.md).
- **corridor is load-bearing.** Every cog except `switchboard` lists it in
  `required_cogs` and loads it via `dependency_loader.ensure_corridor_loaded()`.
  - Permission checks go through `corridor.require_permission` and
    `capabilities_satisfy`.
  - Replies go through `corridor.send_reply`/`render_reply`, never a raw
    `ctx.send` or interaction send. `lint_reply_channel` enforces this.
  - `switchboard` is corridor-free: it uses plain `ctx.send` and is exempt
    from that lint.
- **Cross-cog loading:** Red never loads `required_cogs`. Load dependencies
  with `corridor.dependency_loader.ensure_loaded`/`ensure_importable`. Put
  runtime cross-cog imports inside the function that needs them, never at
  module scope (unless `TYPE_CHECKING`). See
  [`docs/dependency-loading.md`](docs/dependency-loading.md).
- **Writable data** goes only under `cog_data_path(self)`. The installed
  package tree is read-only at runtime.
- **`info.json`:**
  - keys are case-sensitive;
  - every cog needs an `install_msg` naming its outside services and each
    `required_cogs` entry, and must be named in the root `README.md`;
  - all cogs share one `min_bot_version`/`max_bot_version` range. Raise it
    only once switchboard's suite passes on the new Red; `red-release.yml`
    opens that PR. A new private Red API must be listed in
    `docs/dependency-loading.md` and pinned in
    `switchboard/tests/test_red_private_api.py`.
- **Purity:** `corridor/ui_limits.py` stays free of discord/redbot imports.
- **Logging and async:** loggers are named `red.pixel-agents-cogs.<cog>`, and
  nothing blocks inside `async def`. See
  [`docs/runtime-hygiene.md`](docs/runtime-hygiene.md).
- **Generated contracts** under `contracts/` (and `corridor/*.yaml`) are
  regenerated from their source models, never hand-edited. See
  [`docs/contract-testing.md`](docs/contract-testing.md).
- **Style:** Python 3.11, ruff (line length 100), mypy strict. Match the
  surrounding code's naming, comment density and idiom.

## Writing docs, commits, PRs and issues

- **Be short and concise.**
- **Docs describe current behaviour only.** Update the doc a code change
  affects in the same change. If a doc and the code disagree, the code wins.
- **No history and no dates or times:** no "Update:", "used to", "as of",
  "superseded by", and no timestamps. Git records when things changed.
- **No sensitive information in anything public:** repository files,
  commit messages, PRs, issues or comments. Editing it out later doesn't
  help, because GitHub keeps edit history and history is not rewritten.
  Never write:
  - secret values;
  - what kind of credential a secret holds, or what it may access;
  - which account owns a credential, or any account's role or access
    level;
  - whether a secret is set;
  - org or security settings.

  Name a secret by its name and purpose only. Write "the bump PR is opened
  with the `AUTOMATION_TOKEN` secret so its checks run", not what kind of
  token it is, what it can access, or whose it is. Credential setup goes to
  the repo owner privately, never into the repo. When unsure, leave it out.
- **Fix documented drift** you find while touching related code in the same
  change.

## Git

- Branch from `develop`, and use conventional commit messages.
- Tooling may rewrite `.claude/settings.json` locally. Never commit a change
  to it you didn't make on purpose.
