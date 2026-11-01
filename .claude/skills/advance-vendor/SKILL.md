---
name: advance-vendor
description: Move the vendored Pixel Agents pin (pixelagents/infrastructure/webview_vendor.commit + webview_vendor.repo) to a newer upstream commit. Covers diagnosing what the bump breaks in pixelagents/CCTV/architect/painter, fixing it on the cogs side first, building a tracking branch on the nntin-bot fork when the pin must carry unmerged upstream PRs, opening an upstream pixel-agents PR only when this repo genuinely cannot cope alone, keeping the pin in step with the pixatar CLI, and taking over or replacing the automated vendor-update PR. Use for any "bump / update / advance pixel-agents" task.
---

# Advancing the vendored Pixel Agents

There is no submodule here. `pixelagents/` ships two small files next to
`infrastructure/webview_build.py`:

- `webview_vendor.commit`: the 40-character commit to build;
- `webview_vendor.repo`: the git URL to clone it from. It points at upstream
  (`https://github.com/pixel-agents-hq/pixel-agents.git`) whenever the pin is on
  upstream `main`. It points at the `nntin-bot/pixel-agents` fork while the pin
  has to carry upstream PRs that haven't merged. GitHub only serves a commit
  from a repository (or `refs/pull/N/head`) that holds it: `git fetch <upstream> <sha>`
  refuses a fork-only commit with `not our ref`.

At `cog_load` (and `[p]pixelagents webview rebuild`), `ensure_webview_built`
clones that repo into Red's per-cog data dir. It then checks out the pin, runs
`npm ci --workspace=webview-ui --ignore-scripts --no-audit --no-fund` and `vite build --base ./`, and
runs `webview_build_scripts/emit_decoded_assets.ts`. That script imports
upstream's own TypeScript (`core/src/assets/*.ts`,
`core/src/defaultCharacterProfiles.ts`) through `tsx`. Finally `_sync_dist`
copies only what is served into `webview_dist/`. An existing clone is
repointed (`git remote set-url`) before it fetches, so moving the pin between
repositories needs no operator action.

Every host builds this on its own machine, so a bad pin fails on bot hosts,
not just in CI. Three things consume the result, and an upstream bump can
break each of them even when upstream is green:

| Consumer | What it depends on |
| --- | --- |
| `emit_decoded_assets.ts` | upstream module paths and export names |
| `_sync_dist` | the Vite output layout: what `index.html` links |
| CCTV (`cctv/infrastructure/webview.py`, `cctv/application/pipeline.py`) and `pixelagents/application/office.py` | the websocket protocol in upstream's `core/asyncapi.yaml`: bootstrap messages, `agentCreated`/`existingAgents`/`settingsLoaded` fields, inbound `saveAgentSeats` |
| architect / painter | the layout JSON shape, through `pixel_agents_adapter.decode()`/`encode()` |

## Rule 1: fix it here unless you can't

Before proposing anything upstream, write down why this repo can't absorb
the change. Try these first:

| Upstream change | Do this here | Don't |
| --- | --- | --- |
| Renamed fields (`folderName` → `directoryName`, `hueShift` → `repeat`) | Migrate every builder, model, test and the consumer contract to the **new** names | Add compat aliases on either side. This repo's settled rule is to follow upstream's names |
| Removed export (`decodeAllCharacters`, once characters came from the wardrobe) | Use what upstream uses now (`DEFAULT_CHARACTER_PROFILES` → `characterProfilesLoaded`) | Ask upstream to keep the old export |
| New required field (`settingsLoaded.bypassPermissions`) | Send it with the value this office means | Make it optional upstream |
| New file the bundle links (`manifest.webmanifest`, `icons/`) | Copy it in `_sync_dist` | Strip the link |
| Persisted data in an older format (seat records' `hueShift`) | Read it the way upstream's own server does (`seat_repeat` mirrors `legacyRepeat`) and rewrite on the next save | Keep writing the old field |

An upstream PR is justified only when this repo can't reach the behaviour
from outside: a bug in code the bundle runs unmodified. "It belongs upstream"
is a scoping preference, not a reason. When unsure, put the reasoning to the
user first.

## Rule 2: the pin follows the pixatar CLI

pixelagents deals every office character with the pixatar CLI
(`@pixel-index/pixatar`). It builds it at `cog_load` from the Pixel Index
commit in `pixelagents/infrastructure/pixatar_vendor.commit`
(`infrastructure/pixatar_build.py`), and loads the Python client that drives
it from the same checkout (`packages/pixatar/python`), so the two are always
one commit. pixatar bakes in a
Pixel Agents wardrobe, renderer and profile format from the index's vendor pin.
A profile or seed code it produces names wardrobe parts, and the office can
show it only if its build has the same wardrobe.

To find the commit pixatar draws with, use any of these:

```bash
ao-heavy env PIXATAR_CLI= PIXELAGENTS_REAL_PIXATAR_BUILD=1 python3 -m pytest -q --timeout=900 \
  pixelagents/tests/test_pixatar_build.py -k RealPixatarBuild   # builds it, checks the pin contains it
pixatar --version      # names the pixel-agents version
node -e "import('@pixel-index/pixatar').then(m => console.log(m.VENDOR))"   # {version, commit}
```

The real gate asks the CLI's `version` op for `vendor.commit` and checks
`git merge-base --is-ancestor <pixatar-commit> <pin>` against
`webview_vendor.repo`. The wardrobe sources must also match. This should
print nothing:

```bash
git -C <clone> diff --stat <pixatar-commit> <pin> -- \
  webview-ui/src/characters core/src/characterProfiles.ts \
  core/src/defaultCharacterProfiles.ts core/src/wardrobeItems.ts
```

Moving the pin past the CLI's wardrobe is a coordinated change. Bump
`pixatar_vendor.commit` to an index commit whose pixatar draws with the new
wardrobe. Otherwise the office will
know parts the CLI can't draw. Stored characters keep their seed code and
profile. Only members dealt for the first time after a pixatar bump can get a
different look from the same user id.

## Procedure

### 1. Sync and scope

```bash
git fetch origin develop && git checkout -B feat/<issue> origin/develop
S=<scratchpad>
git clone https://github.com/pixel-agents-hq/pixel-agents.git $S/pa
git -C $S/pa remote add fork https://github.com/nntin-bot/pixel-agents.git
git -C $S/pa fetch fork
PIN=$(cat pixelagents/infrastructure/webview_vendor.commit)
git -C $S/pa log --oneline $PIN..<candidate>
git -C $S/pa diff --stat $PIN <candidate> -- core/src/assets core/src/defaultCharacterProfiles.ts webview-ui/public/assets
```

Then diff the protocol for every message the cogs send or parse. Compare the
`properties`/`required` sets of each `components.schemas.*` entry in
`core/asyncapi.yaml`, old pin against candidate. The list is in
`pixelagents/contracts/outbound.py`, `office.py`'s `bootstrap_messages`, and
`cctv/contracts/websocket.py`.

Check for an open automated PR (`chore/vendor-pixel-agents`, opened by
`vendor-update.yml`). Yours supersedes it (step 6).

### 2. Pick the candidate

- **Upstream `main` has everything you need:** pin it, and set
  `webview_vendor.repo` to the upstream URL.
- **You need unmerged upstream PRs:** build a tracking branch on the fork.
  Merge the PRs onto the newest commit you need, resolve conflicts the way
  upstream would, and regenerate upstream's generated files
  (`npm run asyncapi:generate`, `node scripts/generate-e2e-inventory.mjs`,
  CLAUDE.md's message counts). Then check the merge with upstream's own
  tooling (`npm ci --ignore-scripts`, `npm run check-types`,
  `cd webview-ui && npx vitest run --maxWorkers=2`, `npx prettier --check`
  on the touched files). `test/dev-assets.test.ts` starts a Vite dev server
  and fails in a sandbox that can't bind it, so confirm such a failure
  doesn't involve files the merge touched. Push the branch as
  `track/cogs-vendor` on `nntin-bot/pixel-agents`. That is the ref
  `vendor-update.yml` follows. Pin the merge commit and set
  `webview_vendor.repo` to the fork URL.

  The fork branch isn't updated for you. When the upstream PRs move, re-merge
  and push again. The next scheduled `vendor-update.yml` run then proposes
  the new tip. Once they merge, point `webview_vendor.repo` back at upstream
  and pin `main`. Leave the fork branch in place until no pin references it.

### 3. Run the gates (the ones `vendor-update.yml` runs)

All of these are heavy. Run them one at a time through `ao-heavy`. Run each
cog's suite in **its own** pytest process: every cog's `conftest.py`
installs its own `sys.modules["redbot.core"]` stub, and the
`check_pytest_scope.py` hook blocks a combined run.

```bash
PIXELAGENTS_REAL_WEBVIEW_BUILD=1 ao-heavy python3 -m pytest -q pixelagents/tests/test_webview_build.py -k RealWebviewBuild
PIXATAR_CLI= PIXELAGENTS_REAL_PIXATAR_BUILD=1 ao-heavy python3 -m pytest -q --timeout=900 pixelagents/tests/test_pixatar_build.py -k RealPixatarBuild
ao-heavy python3 -m pytest -q pixelagents/tests
ao-heavy python3 -m pytest -q cctv/tests
ao-heavy python3 -m pytest -q architect/
ao-heavy python3 -m pytest -q painter/
# pixel-agents contract lints (offline) and the live check against the real build
python3 -m contracts.pixel_agents.generate_consumer_contract --check
python3 -m contracts.pixel_agents.lint_outbound_contract
python3 -m unittest contracts.pixel_agents.tests.test_generate_consumer_contract \
  contracts.pixel_agents.tests.test_lint_outbound_contract contracts.pixel_agents.tests.test_verify_outbound
ao-heavy python3 -m contracts.pixel_agents.verify
# pixel-index contract lints
python3 -m contracts.pixel_index.lint_endpoints
python3 -m contracts.pixel_index.lint_model_usage
```

`contracts.pixel_agents.verify` is the gate that catches protocol drift.
It builds the pin for real and validates every message the cogs emit against
the pinned `asyncapi.yaml`. It also checks that each asset `index.html` links
resolves in `webview_dist/`. The real-build pytest gate checks less.

Also run the e2e suite, which drives the real bundle in Chromium. It isn't a
`vendor-update.yml` gate, but it's the only thing that loads the office the
way a browser does. Build once and reuse it. Without the cache, every test
rebuilds within the 30s `pytest-timeout`, and on a loaded host the failures
are timeouts, not regressions:

```bash
PIXELAGENTS_E2E_WEBVIEW_CACHE=$S/e2e-cache PIXELAGENTS_REAL_WEBVIEW_BUILD=1 ao-heavy python3 -m pytest -q e2e/
```

### 4. Fix here, then verify everything

Fix in the layer that owns the dependency:
- the emit script for module or export changes;
- `_sync_dist` for output layout changes;
- `outbound.py` / `office.py` / `cctv/contracts/websocket.py` for protocol changes.

After touching `outbound.py`, regenerate the committed contract
(`python3 -m contracts.pixel_agents.generate_consumer_contract`). Never edit
it by hand. Update the test fixtures that fake a Vite build
(`write_fake_vite_build` in `pixelagents/tests/conftest.py`, mirrored in
`floorplan/tests/conftest.py`) to the new layout. Update the docs that
describe it in the same change (`docs/contract-testing.md`,
`docs/cctv-design.md`, `pixelagents/Architecture.md`). Then rerun step 3 at
the final pin, plus ruff and mypy over the cogs (the command in
`.claude/CLAUDE.md`). Don't format `contracts/` with ruff: CI doesn't, and
it rewrites unrelated files.

### 5. Only if an upstream change is genuinely needed

`nntin-bot` can't push to `pixel-agents-hq/pixel-agents`. Branch from
upstream `main` in your clone and push it to the fork
(`git push -u fork fix/<topic>`). Then open **two** PRs from it:

1. **Upstream:** `gh pr create --repo pixel-agents-hq/pixel-agents --head nntin-bot:fix/<topic> --draft`,
   filling in `.github/pull_request_template.md`. Upstream's `pr-title.yml`
   requires a conventional-commit title whose subject starts lowercase.
2. **CI mirror** inside the fork, against the fork's `main` (sync it to
   upstream first), marked "CI mirror of #N, don't merge". Upstream's
   pipeline doesn't run on a cross-fork PR.

Once the upstream PR exists, its commits are also served from upstream via
`refs/pull/N/head`. Keep the pin on the fork's `track/cogs-vendor`
regardless: the build fetches branches, not pull refs.

### 6. Watch CI, close what you supersede

- This repo: `gh pr checks <N> --repo pixel-agents-hq/pixel-agents-cogs --watch`
  (in the background). `cogs-quality.yml`, `contract-checks.yml` and
  `e2e.yml` all trigger on both pin files.
- The fork mirror, if any: `gh api "repos/nntin-bot/pixel-agents/actions/runs?per_page=10"`.
  No runs at all means the fork's Actions are disabled. An owner has to
  enable them in the fork's Actions tab.
- An automated `chore/vendor-pixel-agents` PR for the same range: comment
  with what yours adds, then close it. Put `Supersedes #N` in your body.
- Upstream or fork PRs that turned out unnecessary: close both, with a short
  apology upstream saying why.

`gh pr edit` needs `read:org`, which the bot token lacks. Edit with
`gh api -X PATCH repos/<owner>/<repo>/pulls/<N> -f title=... -F body=@file`.

### 7. The PR body

- Each upstream change that broke something, and the fix for each.
- What you checked and found unbroken.
- A table of the gates actually run against the final pin, with results.
- Where the pin lives (upstream `main` or the fork's tracking branch, and
  which upstream PRs it carries).
- The pixatar `VENDOR.commit` it matches.
- Any merge-order dependency.
