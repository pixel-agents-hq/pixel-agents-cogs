# switchboard

Install requirements for `[p]addpath` cogs, and load/unload cogs in
dependency order.

## Overview

Red installs a cog's `info.json` `requirements` only when the cog is
installed through Downloader (`[p]repo add` + `[p]cog install`). A cog
loaded from an `[p]addpath` directory (or a `--cog-path`) gets nothing
installed: `[p]load corridor` fails with
`ModuleNotFoundError: No module named 'a2a'`. Red also never reads
`required_cogs`, so it cannot load several interdependent cogs in the right
order. When a cog fails during startup, Red removes it from its autoload
list.

Switchboard fills those gaps:

- **Installs requirements.** It runs Downloader's own command,
  `python -m pip install -U -t <data>/cogs/Downloader/lib ...`, into the
  directory Red already puts on `sys.path`, so new packages import without a
  restart. Each run is one pip call with every requirement of the cogs
  involved and of every loaded or managed cog, so pip's resolver sees every
  version pin together.
- **Loads in order.** It loads a cog and everything in its `required_cogs`,
  dependencies first, through Red's own load logic, so loaded cogs land in
  Red's autoload list. If a dependency fails, its dependents are skipped
  with a reason, and unrelated cogs still load.
- **Unloads in order.** It unloads a cog and every loaded cog that requires
  it, dependents first.
- **Reports where each cog comes from.** The source is `downloader`
  (`[p]cog install`, with the repo name), `downloader_untracked` (in Red's
  install path but unknown to Downloader), `addpath`, `cli_path`
  (`--cog-path`), or `core`. A copy hidden by a higher-priority path is
  reported as shadowed. Downloader's install path always wins over addpath.
- **Autorepairs.** Every cog switchboard loads becomes *managed*. Once Red
  is ready after a restart, switchboard installs missing requirements and
  loads every managed cog that is not loaded. This is on by default. A cog
  stays managed until `[p]switchboard unload` unloads it: after a plain
  `[p]unload`, autorepair loads it again on the next restart.

Switchboard has no `requirements` and no `required_cogs`. It uses only what
Red ships, so it loads in an environment where corridor cannot import. For
the same reason it replies with plain messages instead of through
corridor's reply styling.

## Installing

Either route works, and `[p]switchboard status switchboard` tells you which
one you used.

```
[p]addpath /cogs/office-cogs
[p]load switchboard
```

```
[p]repo add pixel-agents-cogs https://github.com/pixel-agents-hq/pixel-agents-cogs
[p]cog install pixel-agents-cogs switchboard
[p]load switchboard
```

For a fresh container that should set itself up, start Red with the
checkout as a cog path and switchboard preloaded. Autorepair then restores
the managed set on every start:

```
redbot <instance> --cog-path /cogs/office-cogs --load-cogs switchboard
```

Red ignores `--load-cogs` on an instance's very first start, and on the
first start after a Python minor-version change. In both cases it only
records the Python version and loads nothing. Load switchboard once by hand
then (`[p]load switchboard`, or RPC `CORE__LOAD`). Every later start loads
it from `--load-cogs` and from Red's own autoload list.

## Commands

Bot owner only. `<cogs>` are cog names, `all` (every enabled cog on an
addpath or `--cog-path` directory, except switchboard), or a directory name
such as `office-cogs`.

| Command | Description |
|---|---|
| `[p]switchboard status [cogs…]` | Source, loaded state, missing requirements and path of each cog. With no cogs: every cog that is not one of Red's own. |
| `[p]switchboard install <cogs…>` | Install the requirements of the cogs and everything they require, even if they already look satisfied. |
| `[p]switchboard load <cogs…>` | Install missing requirements, then load the cogs and everything they require, dependencies first. The loaded cogs become managed. |
| `[p]switchboard unload <cogs…>` | Unload the cogs and every loaded cog that requires them, dependents first. They stop being managed. |
| `[p]switchboard reload <cogs…>` | `unload`, then `load` the same cogs plus the dependents that were unloaded. |
| `[p]switchboard repair` | Load every managed cog that is not loaded. |
| `[p]switchboard autorepair [true\|false]` | Show or set whether managed cogs are repaired once the bot is ready (default: on). |
| `[p]switchboard managed` | List the managed cogs. |

## RPC

With Red started with `--rpc`, the same operations are available as
`SWITCHBOARD__STATUS`, `SWITCHBOARD__INSTALL`, `SWITCHBOARD__LOAD`,
`SWITCHBOARD__UNLOAD` and `SWITCHBOARD__REPAIR`. Each takes one list of
cogs (JSON-RPC params `[["corridor"]]`, the same shape as Red's
`CORE__LOAD`; `REPAIR` takes none) and returns a JSON report.
[`.github/workflows/switchboard.yml`](../.github/workflows/switchboard.yml)
drives a real bot through them.

## Limits

- A requirement counts as satisfied when the distribution Python would
  import matches it. Red appends Downloader's lib directory *after*
  site-packages, so a package pinned in a cog's `info.json` but installed
  at a different version in Red's own environment still shadows the copy
  pip installs. Switchboard reports such a requirement as still missing
  after the install.
- A bare VCS/archive URL requirement is checked by name only (from
  `#egg=` or the URL's last path segment), at any version.
- `required_cogs` entries that no cog path provides are reported as
  unknown. They do not block loading; the dependent's own `setup()` decides.

## Docs

See [`Architecture.md`](Architecture.md) for the layer boundaries, and
[`docs/dependency-loading.md`](../docs/dependency-loading.md) for how this
fits next to the other cogs' own dependency loading.
