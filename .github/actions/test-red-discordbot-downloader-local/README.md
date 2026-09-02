# test-red-discordbot-downloader-local

Vendored copy of [`nntin/d-flows/actions/test-red-discordbot-downloader`](https://github.com/NNTin/d-flows/tree/873892e7d5f5fa19737b93e01f608f52a8f65a0f/actions/test-red-discordbot-downloader), pinned at `NNTin/d-flows@873892e7d5f5fa19737b93e01f608f52a8f65a0f`. Kept in this repo so a CI incident can be reproduced and iterated on locally without waiting on upstream changes to the shared action. On top of the vendored copy, this adds the `unload_scope` input (see below) so `check-cogs.yml` can exercise both a cold-start and a warm-start dependency-loading scenario per cog.

## Overview
This composite action provisions a git repository containing your cogs (a local throwaway one, or a real remote one via `repo_url` -- this repo's own `check-cogs.yml` uses the latter, pointing back at itself), adds it to Red-DiscordBot through the Downloader cog, installs the cogs using `repo add`/`cog install`, and validates them via Red's RPC interface. This is the only action vendored into this repo today -- there is no local `test-red-discordbot` sibling here; the comparison table at the end describes the *upstream* d-flows action of that name, kept for context on why this heavier variant was chosen.

## Inputs
| Name | Required | Default | Description |
| --- | --- | --- | --- |
| `token` | ✅ | – | Discord bot token passed to the `redbot` run. |
| `cog_paths` | ❌ | `""` | Comma-separated list of local cog directories to mirror into a temporary throwaway repo. Leave empty with `repo_url` unset to auto-discover every top-level directory containing an `info.json` in the runner's workspace (`discover_local_cog_paths` in `test_downloader_cogs.py`); leave empty with `repo_url` set to test every cog Downloader finds in that remote instead. |
| `repo_name` | ❌ | `test-repo` | Friendly label used when the downloader registers the repository; normalized through Downloader's own `RepoManager.validate_and_normalize_repo_name`. |
| `repo_url` | ❌ | `""` | When set, Downloader clones and installs directly from this remote git URL and the action skips building a local throwaway repo. This repo's own CI always sets this to its own `github.repository`, so PR branches are tested exactly as an end user's `[p]repo add`/`[p]cog install` would see them. |
| `repo_branch` | ❌ | `""` | Optional branch name to checkout after cloning. Leave empty to let Downloader detect the default branch or use the local repo's branch. |
| `rpc_port` | ❌ | `6133` | Port the Red RPC server will listen on. Keep default unless you have networking conflicts. |
| `unload_scope` | ❌ | `cog` | `cog` unloads only the cog under test before its load/unload cycle (its `required_cogs` may already be loaded from an earlier cog's turn -- the warm-start case). `cog-and-dependencies` also unloads everything in its `required_cogs` (transitively) first, forcing a genuine cold-start dependency bootstrap. |

## Usage example

This repo's own real usage, from [`../../workflows/check-cogs.yml`](../../workflows/check-cogs.yml)
(no `cog_paths` -- it tests every cog Downloader finds by cloning this
repo's own PR branch via `repo_url`/`repo_branch`):

```yaml
jobs:
  test-cogs:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        unload_scope: [cog, cog-and-dependencies]
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - run: pip install red-discordbot
      - uses: ./.github/actions/test-red-discordbot-downloader-local
        with:
          token: ${{ secrets.DISCORD_BOT_TOKEN }}
          repo_url: https://github.com/${{ github.repository }}
          repo_branch: ${{ github.head_ref || github.ref_name }}
          unload_scope: ${{ matrix.unload_scope }}
```

A local-only invocation (no `repo_url`, an explicit `cog_paths`) is also
supported for testing a cog directory that isn't pushed anywhere yet:

```yaml
      - uses: ./.github/actions/test-red-discordbot-downloader-local
        with:
          token: ${{ secrets.DISCORD_BOT_TOKEN }}
          cog_paths: cogs/example,cogs/second
          repo_name: pr-${{ github.event.number || github.run_id }}
```

## How it works
1. The action installs the `aiohttp`/`jsonschema` dependencies used by the RPC test client.
2. If `repo_url` is empty and `cog_paths` is empty, the runner's own workspace is scanned for every top-level directory containing an `info.json` and that becomes the cog list. If `repo_url` is empty and `cog_paths` is given, the action creates a temporary throwaway git repo, copies just the requested cog directories into it, and commits it locally. If `repo_url` is set, both of the above are skipped and Downloader talks to that remote directly (with `cog_paths` left empty, every cog Downloader finds there is tested).
3. Red-DiscordBot starts in the background (`--rpc --rpc-port <rpc_port>`, using `--no-instance` if no configured instance is found) and writes logs to `${{ runner.temp }}`.
4. The helper script `test_downloader_cogs.py` loads Red's configuration, initializes the downloader `RepoManager`, and adds the temporary git repo via `repo add` semantics.
5. Downloader installs each cog into Red's configured install path, ensuring requirements are installed into Downloader's library directory.
6. Before any real cog is exercised, a minimal fixture "dashboard" cog (`fixtures/dashboard/`) is copied straight into Red's install path (bypassing Downloader) and loaded via RPC, satisfying cctv's `dashboard_cog_loaded()` check (`cctv/adapters/dashboard.py`; this used to be floorplan's check before its dashboard responsibilities moved to cctv) so its `cog_load()` doesn't DM the real bot owner every run just because Red Web Dashboard (an external cog) isn't installed in CI. It's never part of the repo's own cog list, so it's never load/unload-exercised itself.
7. Using the RPC websocket endpoint, every installed cog is loaded and then unloaded to verify that the installation truly works inside a live bot.
8. On success or failure the action cleans up: the fixture dashboard cog and installed cogs are removed, Red is stopped, the downloader clone is deleted, and the temporary repository is discarded.

## Requirements
- Git 2.x, Python 3.11+, and Red-DiscordBot itself (e.g. `pip install red-discordbot`) must be available on the runner image. Red-DiscordBot does **not** need to be pre-configured: if `~/.config/Red-DiscordBot/config.json` is missing, the action automatically runs Red with `--no-instance` instead of failing.
- Downloader relies on cog `info.json` metadata being present and valid JSON.
- With `cog_paths` given, each named cog must live in its own directory. With it empty, either every `info.json`-containing top-level directory in the workspace (no `repo_url`) or every cog Downloader finds in the remote (`repo_url` set) is used instead.

## Troubleshooting
- **Invalid repo name**: Downloader repo names must be valid identifiers; `normalize_repo_name` converts hyphens to underscores and runs Downloader's own `RepoManager.validate_and_normalize_repo_name`, but other invalid characters will still raise errors.
- **Requirement installation failures**: Review the action log to see which dependency pip command failed; you may need to add wheels or pin compatible versions.
- **RPC timeout**: The helper waits 30 seconds for the websocket endpoint (`RPC_WAIT_TIMEOUT` in `test_downloader_cogs.py`). Check the Red log tail dumped by the action when failures occur.
- **Git errors**: Verify each cog directory includes necessary files and can be committed. Ensure your cogs don't include very large binaries or files requiring Git LFS.
- **Remote-only installs**: When `repo_url` is set the action does not mirror local files. Make sure the specified remote repo already contains the cogs you expect to load; leave `cog_paths` empty to test every cog Downloader finds there, or set it to match a subset by name.
- **Known limitations**: The helper assumes Downloader installs requirements with pip and the auto-generated local repo (the `cog_paths`-without-`repo_url` path) uses a `master` branch; specify `repo_branch` if your remote uses a different branch layout.
- **Custom repo branches**: Use `repo_branch` when your remote's default branch isn't detected automatically (for example `main` or feature branches).

## Comparison with `test-red-discordbot`

`test-red-discordbot` is the lighter-weight upstream `d-flows` action this
one was chosen over -- it is not vendored into this repo, so there is no
local copy to link to here; this table just records the tradeoff that
motivated pulling in the heavier downloader-based variant instead.

| Capability | `test-red-discordbot` | `test-red-discordbot-downloader` |
| --- | --- | --- |
| Installs via downloader git repo | ❌ copies directories directly | ✅ clones and installs through downloader |
| Validates `repo info.json` metadata | ❌ | ✅ |
| Installs requirements using downloader logic | ❌ (manual `uv pip install`) | ✅ (repo-managed pip install) |
| Exercises load/unload through RPC | ✅ | ✅ |
| Detects downloader-specific regressions (repo config, git hooks, metadata) | ❌ | ✅ |

Use the downloader variant when you need high confidence that your cogs can be installed by end users through `[p]repo add` and `[p]cog install`. Keep using the RPC-only version for faster smoke tests when git/dependency flows are not critical.
