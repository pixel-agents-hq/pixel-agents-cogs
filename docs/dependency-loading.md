# Cross-cog dependency loading

Every cog in this repo except `corridor` depends on at least one other cog
in the same repo (`pixelagents` needs `corridor`; `floorplan` needs both).
Red-DiscordBot has no built-in mechanism for this — confirmed against Red's
own source (`redbot/core/bot.py`'s `load_extension`/`_pre_connect`,
`redbot/core/core_commands.py`'s `_load`/`_reload`, and the entire
`redbot/core/_downloader/` package never read `required_cogs` at load or
install time). Red's own cog-creator docs say so explicitly:

> `required_cogs` (dict mapping a cog name to repo URL) — a dict of required
> cogs that this cog depends on... **Downloader will not deal with this
> functionality but it may be useful for other cogs.**

So `required_cogs` in `info.json` is descriptive metadata for humans, not
something Red enforces. Every cog here hand-rolls its own dependency
loading in `setup()`, via `bot._cog_mgr.find_cog(name)` +
`bot.load_extension(spec)` — the same two calls Red's own loader uses
internally. This doc is the single place that explains the resulting
pattern and why it has two different shapes.

## The three tools

All three live in `corridor/dependency_loader.py` (except bootstrapping
corridor itself — see below):

| Tool | What it does | When to use it |
|---|---|---|
| `ensure_loaded(bot, package, cog_name)` | Fully loads the dependency as a registered Red Cog (`bot.load_extension` + `bot.add_loaded_package`). Returns the live Cog. | The dependency is tested *before* you by the CI smoke test (see below), or you have a genuine synchronous need for a live instance right away (e.g. calling `register_dependent` in your own `cog_load()`). |
| `ensure_importable(bot, package)` | Makes `import <package>` resolvable (`spec.loader.load_module()`, the same step `load_extension` does internally) *without* registering it as a loaded Cog. | You need cross-module imports at `setup()` time (e.g. `from pixelagents.domain import AgentKey` at the top of an adapter file) but don't need a live Cog instance yet. |
| `LazyDependency(bot, package, cog_name)` | A resolve-once handle: `.resolve()` lock-guarded full-loads on first call and caches; `.eager_load_in_background()` does the same but swallows/logs failures, meant to be scheduled (not awaited) from `cog_load()`; `.value` is the cached Cog or `None`. | The dependency is tested *after* you by the CI smoke test, or you just don't need it synchronously at load time. This is the default recommendation for depending on any cog other than corridor. |

## Why two different loading *strategies* exist

The `.github/workflows/check-cogs.yml` job (`nntin/d-flows/actions/test-red-discordbot-downloader@v1`)
loads and tests each cog **one at a time, in isolation, alphabetically**
(`corridor` → `floorplan` → `pico` → `pixelagents` → `toolbox`). After
loading a cog, it checks whether Red reports it under `loaded_packages`
(fresh load) or `alreadyloaded_packages` (Red already considered it
loaded) — the latter is treated as a **failure** for that cog's own turn,
because it isn't proof that cog's `[p]load` truly works from a clean
state; it's proof some earlier cog silently dragged it in.

This makes the safe choice for "should my dependent do a synchronous full
`ensure_loaded` of its dependency?" depend on alphabetical position:

- **corridor** sorts first. Nothing depends-loads it before its own turn,
  so every dependent can safely `ensure_loaded`/hand-roll-bootstrap it
  synchronously. This also happens to be a real functional requirement,
  not just a CI accident: every dependent calls
  `self._corridor.register_dependent(<name>)` synchronously inside its own
  `cog_load()`, which needs a live corridor instance immediately.
- **pixelagents** sorts *after* **floorplan**. If floorplan's `setup()`
  synchronously `ensure_loaded`-ed pixelagents as a side effect, the smoke
  test would find pixelagents `alreadyloaded_packages` when it got to
  testing pixelagents on its own turn, and fail. floorplan also has no
  synchronous need for a live pixelagents instance at `cog_load()` time —
  only `_sync_webview_assets()`, triggered later by an actual webview
  request, needs one. So floorplan uses `ensure_importable` (synchronous,
  for the module-scope imports) plus `LazyDependency` (for the eventual
  live instance, resolved lazily on first use or eagerly in the
  background) — see `floorplan/adapters/cog_base.py`.

The rule in one sentence: **use `ensure_loaded` only when you have a real
synchronous need for a live instance, or the dependency is corridor;
use `LazyDependency` for everything else.**

## corridor's bootstrap is unavoidably duplicated

`ensure_corridor_loaded` (hand-rolled `find_cog`/`load_extension`, not
going through `corridor.dependency_loader`) is duplicated verbatim in
every dependent's own `dependency_loader.py` (`floorplan/`, `pixelagents/`,
`toolbox/`, `pico/`, and the `.cookiecutter/cog-cookiecutter` template new
cogs are generated from). This is structural, not an oversight: you cannot
`from corridor.dependency_loader import ensure_loaded` before corridor
itself is loaded and importable. Once corridor *is* loaded, every other
cross-cog dependency goes through the shared `corridor.dependency_loader`
tools above instead of each dependent hand-rolling its own pair.

## Module-scope imports of an unloaded dependency are a landmine

Red's `_load` always calls `_cleanup_and_refresh_modules(spec.name)`
*before* `bot.load_extension(spec)` — this re-execs every already-cached
`sys.modules` entry matching the package or its submodules, unconditionally,
regardless of whether this is a fresh load or a reload. If a heavily-cached
module (e.g. a Cog's own `adapters/cog_base.py`, imported by every other
adapter file) has a hard `from corridor.domain import X` at module scope,
that import re-runs on every reload attempt — including one made at a
moment corridor isn't currently loaded — and crashes with
`ModuleNotFoundError` before `setup()` ever gets a chance to load it.

This bit production once: `pixelagents/adapters/replies.py` had a
module-scope `from corridor.domain import ReplyField`, used only in type
annotations. Fix: move annotation-only cross-cog imports under
`if TYPE_CHECKING:` (see `pixelagents/adapters/replies.py`,
`floorplan/adapters/replies.py`); where a name is actually constructed at
runtime (not just annotated), defer the import into the function body that
needs it instead of module scope (see `floorplan/adapters/admin_commands.py`'s
`cmd_status`, and `corridor.dependency_loader.LazyDependency`/`ensure_loaded`
imports inside `floorplan/adapters/cog_base.py`'s `__init__`/methods rather
than its top-level import block). `pixelagents/__init__.py`'s module
docstring has the full mechanical trace of the incident.
