# Red Downloader and Git Submodules

This document records whether a Red-DiscordBot cog repository may contain git
submodules and whether Downloader keeps them correct through clone, update,
revision checkout, and cog installation.

The findings were verified against the local Red-DiscordBot `V3/develop`
checkout at commit `61484f28f7fcfff81309cb2b0b0d5287c921f1ad`.

## Conclusion

Red's Downloader **allows cog repositories to contain git submodules**. A
repository added with `[p]repo add` is cloned with `--recurse-submodules`, so
its submodules and nested submodules are populated during the initial clone.

Downloader does **not** recursively synchronize submodules on every git
checkout. Its checkout command is a plain `git checkout <revision>`: it does
not use `--recurse-submodules`, `git submodule sync`, or
`git submodule update --init --recursive`. A system-level Git setting such as
`submodule.recurse=true` could change local Git behavior, but Downloader does
not set or require it. Code must therefore not assume that a submodule
worktree matches the gitlink recorded by a temporarily checked-out revision.

For office-cogs, the safe current architecture is to consume Pixel Index over
HTTP and ship any assets needed by the cog in the cog package. A Pixel Index
submodule is not required for that integration, and office-cogs has no git
submodules and no `.gitmodules` file.

The Pixel Agents webview raises the same question as Pixel Index's
vendoring: how does a top-level, build-time-only dependency reach an
installed cog when Downloader will neither copy nor build one? office-cogs
answers it without a submodule at all: no `webview_dist/` build output is
committed anywhere in the repo. Instead,
`pixelagents/infrastructure/webview_build.py` clones the pinned commit
(`pixelagents/infrastructure/webview_vendor.commit`, shipped *inside*
`pixelagents/` for the same "Downloader only copies this directory" reason
the pin file for a hypothetical nested submodule would need to be) and runs
the frontend build itself, from inside the installed cog, at `cog_load`
time, into `redbot.core.data_manager.cog_data_path(self)` rather than
`pixelagents/` or any Downloader-managed path. This sidesteps the whole
submodule question: there is no submodule, top-level or nested, for
Downloader to mishandle, and the build runs where Downloader's copy step
already can't reach — after install, inside the cog's own code. See
`pixelagents/Architecture.md`'s "Webview build" section for the full
mechanism, including the `BuildOutcome` (`ok`/`missing_tools`/`status_line`)
a failed build reports without ever failing `cog_load` itself — a case a
repo-root submodule could never have needed to think about.

## What Downloader runs

The relevant commands are constants on `Repo` in
`redbot/core/_downloader/repo_manager.py`:

| Operation | Downloader command | Submodule behavior |
|---|---|---|
| Clone a named branch | `git clone --recurse-submodules -b <branch> <url> <folder>` | Initializes the submodules recorded by the cloned revision, recursively. |
| Clone the default branch | `git clone --recurse-submodules <url> <folder>` | Same. |
| Update the tracked branch | `git pull --recurse-submodules -q --ff-only` | Asks Git to recurse into submodules involved in the pull. |
| Checkout a commit, tag, or branch | `git -C <folder> checkout <revision>` | Does not explicitly recurse, initialize, synchronize, or update submodules. |
| Reset before an update | `git -C <folder> reset --hard origin/<branch> -q` | Does not explicitly recurse into submodule worktrees. |

The clone commands are defined at lines 97–103, pull at lines 108–111, and
checkout at line 118. `Repo.clone()` executes the recursive clone at lines
639–670. `Repo._checkout()` executes only the plain checkout at lines 581–598.
`Repo.update()` performs the reset and recursive pull at lines 809–835.

This means the initial clone supports even nested submodules. Normal branch
updates receive Git's recursive-pull handling, but Downloader has no explicit
repair step for an uninitialized submodule, a changed submodule URL, or a
submodule left at the wrong commit by a revision checkout. In particular,
installing or inspecting a pinned revision can check out a different
superproject commit without checking out the submodule commit recorded by that
revision.

## Checkout and install locations

Downloader maintains two separate trees:

1. **Repository worktree.** `RepoManager.repos_folder` is
   `data_manager.cog_data_path(RepoManager) / "repos"`. With Red's standard
   data-path layout, a repository named `office-cogs` is therefore under:

   ```text
   <DATA_PATH>/<COG_PATH_APPEND>/RepoManager/repos/office-cogs
   ```

   A submodule at `vendor/pixel-index` would be checked out below that worktree
   at `vendor/pixel-index`, according to the path in `.gitmodules`.

2. **Installed cog tree.** Downloader copies the selected installable—here,
   the `pixelagents/` directory—to the Cog Manager install path. The default is:

   ```text
   <DATA_PATH>/<COG_PATH_APPEND>/CogManager/cogs/pixelagents
   ```

   The bot owner can run `[p]paths` to see the effective install path, which
   may have been configured to another directory.

The repository path comes from `RepoManager.repos_folder` at
`redbot/core/_downloader/repo_manager.py:1027–1030`. The default install path
comes from `redbot/core/_cog_manager.py:47–51`, and `[p]paths` reports it. The
copy itself is `shutil.copytree()` in
`redbot/core/_downloader/installable.py:121–143`.

The distinction matters:

- A top-level submodule such as `vendor/pixel-index` exists in Downloader's
  repository worktree but is **not** copied when Downloader installs only
  `pixelagents/`.
- A submodule nested inside `pixelagents/` is within the copied directory, so
  its populated worktree files are copied into the install path. This still
  depends on the submodule being at the correct revision before the copy.
- Installed cogs run from the install tree, not from Downloader's repository
  worktree. Runtime imports and asset paths cannot reach a top-level submodule
  by using paths relative to the installed cog.

## Can Downloader build from a submodule?

Not by itself. The cog installation flow installs Python requirements and then
copies the selected cog directory. It has no hook for `npm ci`, a frontend
build, code generation, or an arbitrary repository script. The copy path is
visible in `redbot/core/_downloader/__init__.py:362–399`; the wider install flow
is at lines 585–646.

Consequently:

- A user running the ordinary `[p]repo add` and `[p]cog install` flow should
  receive a ready-to-run cog.
- If a submodule is used only as release-time source, maintainers or CI could
  build from it before release and commit or otherwise package the generated
  files inside `pixelagents/`. office-cogs took a different path for its one
  build-time dependency (Pixel Agents' webview) instead: build from the
  installed cog itself, at `cog_load`, into Red's per-cog data directory. See
  the Conclusion above and `pixelagents/Architecture.md`. That avoids a
  release step that can drift from what actually ships, at the cost of the
  build tools (git/node/npm) needing to be present on the bot's own host, not
  just on a release machine -- which is why that path has to degrade
  gracefully (a clear status, an owner DM, a manual rebuild command) rather
  than assume they always are.
- Requiring every bot owner to locate Downloader's repository worktree and run
  a manual build is possible operationally, but it is outside Downloader's
  install/update contract and can be overwritten by later updates.
- A reliable revision-aware build based directly on submodules would first
  require Downloader to synchronize and run
  `git submodule update --init --recursive` after every superproject checkout,
  plus an explicit build mechanism. Current Downloader provides neither.

## Maintainer checks

Use these commands in a cog repository to determine whether it actually
contains submodules and whether their worktrees match the recorded gitlinks:

```sh
# Declared submodules and their configured paths/URLs.
git config -f .gitmodules --get-regexp '^submodule\..*\.(path|url)$'

# Gitlink entries recorded by the current commit (mode 160000).
git ls-files --stage | awk '$1 == "160000"'

# Initialized state and recorded/checked-out commit agreement, recursively.
git submodule status --recursive
```

For a future office-cogs submodule, validate all three commands after initial
clone, ordinary update, pinned-revision install, and switching back to the
tracked branch. The pinned-revision case is the gap in the current Downloader
implementation.
