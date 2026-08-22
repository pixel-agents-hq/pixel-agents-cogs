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
loading in `setup()`/`cog_load()`, via `bot._cog_mgr.find_cog(name)` +
`bot.load_extension(spec)` — the same two calls Red's own loader uses
internally. This doc is the single place that explains the resulting
pattern.

## The two tools

Both live in `corridor/dependency_loader.py` (except bootstrapping corridor
itself — see below):

| Tool | What it does | When to use it |
|---|---|---|
| `ensure_loaded(bot, package, cog_name)` | Fully loads the dependency as a registered Red Cog (`bot.load_extension` + `bot.add_loaded_package`). Returns the live Cog. | Whenever you have a synchronous need for a live instance — which in practice is every cross-cog dependency here: corridor (every dependent calls `register_dependent` synchronously in its own `cog_load()`) and pixelagents-from-floorplan alike. |
| `ensure_importable(bot, package)` | Makes `import <package>` resolvable (`spec.loader.load_module()`, the same step `load_extension` does internally) *without* registering it as a loaded Cog. | You need cross-module imports at `setup()` time (e.g. `from pixelagents.application.office import OfficeService` at the top of `floorplan/adapters/cog_base.py`) but the full Cog load itself needs to happen elsewhere/later. floorplan's `setup()` uses this for pixelagents before importing `.floorplan` (which pulls in that module-scope import), then `cog_load()` separately does the real `ensure_loaded` once the Cog instance is actually needed. |

There used to be a third tool, `LazyDependency`, for a dependency tested
*after* the caller by the CI smoke test (see below) that shouldn't be
force-loaded as a side effect of the caller's own load. It resolved lazily
on first use, with a background eager-load fired from `cog_load()`. It was
removed after a real incident: its background load ran a `git`/`npm`/`vite`
build inside `asyncio.to_thread`, and when that task got cancelled (a
`TaskSupervisor.shutdown()` on unload, or Red's own 30s `_pre_connect`
timeout), the cancellation stopped the *coroutine* but not the OS thread or
subprocess already running inside it — an orphaned build kept running and
later collided with a second, independent build attempt against the same
git working tree (`.git/index.lock` already exists). See "no asyncio-level
lock guards a build" below for why an asyncio lock couldn't have prevented
this, and floorplan's `cog_base.py` for the current, plain `ensure_loaded`
call that replaced it.

## The CI smoke test, and the tradeoff we accept

The `.github/workflows/check-cogs.yml` job (`nntin/d-flows/actions/test-red-discordbot-downloader@v1`)
loads and tests each cog **one at a time, in isolation, alphabetically**
(`corridor` → `floorplan` → `pico` → `pixelagents` → `toolbox`). After
loading a cog, it checks whether Red reports it under `loaded_packages`
(fresh load) or `alreadyloaded_packages` (Red already considered it
loaded) — the latter is treated as a **failure** for that cog's own turn,
because it isn't proof that cog's `[p]load` truly works from a clean
state; it's proof some earlier cog silently dragged it in.

Since `pixelagents` sorts *after* `floorplan`, and floorplan's `cog_load()`
now does a plain, synchronous `ensure_loaded(bot, "pixelagents", ...)` (the
same pattern used for corridor), the smoke test will very likely find
pixelagents already loaded when it reaches pixelagents' own turn, and fail
it. **This is a known, accepted tradeoff**, not an oversight: the
alternative (a background/lazy load, i.e. the removed `LazyDependency`)
traded a CI-only cosmetic failure for a real, reproducible production
incident. Fixing the CI ordering assumption itself would mean changing the
external `nntin/d-flows` action, which is out of scope for this repo — we
design around its documented behavior, not against it.

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
`cmd_status`, and the `ensure_corridor_loaded`/`ensure_loaded` imports
inside `floorplan/adapters/cog_base.py`'s `cog_load()` rather than its
top-level import block). `pixelagents/__init__.py`'s module docstring has
the full mechanical trace of the incident.

**This can't be fixed by making corridor "not unloadable while dependents
exist."** Red's own startup autoload order is not guaranteed — this
session's own bot log showed corridor getting side-effect-loaded out of its
configured autoload slot by a dependent's own bootstrap, producing a
harmless but real `PackageAlreadyLoaded` message. A plain `[p]load
floorplan` (or Red's own autoload) can try to exec floorplan's module
before corridor has ever been imported at all in that process — and Python
evaluates top-level import statements before `setup()`/`cog_load()` even
exists as a callable, so no async bootstrap logic can run first no matter
what. Deferred (function-body) imports of anything that touches corridor at
runtime are load-bearing for this reason, not coding debt to remove.
`TYPE_CHECKING`-guarded imports are unaffected (never executed at runtime)
and stay at the top of the file where they already are.

## No asyncio-level lock guards a build

`pixelagents/infrastructure/webview_build.py::ensure_webview_built` takes a
real OS-level lock (`fcntl.flock` on a `webview_build.lock` file in the
cog's data directory) around the actual `git`/`npm`/`vite` steps, not an
`asyncio.Lock`. The build runs inside `asyncio.to_thread` — a blocking
subprocess on a real OS thread — and `asyncio.Task.cancel()` can only stop
the coroutine awaiting that thread; it cannot kill the thread or the
subprocess already running inside it. An `asyncio.Lock` releases the moment
its `async with` block unwinds on `CancelledError`, which would let a
second caller start a second, genuinely concurrent build against the same
git working tree while the first (cancelled-but-still-running) build is
still in flight — this is exactly the removed `LazyDependency`'s failure
mode. A `flock` is held by the OS against the open file descriptor for as
long as the process holding it is alive, independent of what happens to any
asyncio Task above it, so a second caller queues instead of racing.
