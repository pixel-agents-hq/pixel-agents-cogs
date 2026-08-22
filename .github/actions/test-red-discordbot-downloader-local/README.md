# test-red-discordbot-downloader-local

Vendored copy of [`nntin/d-flows/actions/test-red-discordbot-downloader`](https://github.com/NNTin/d-flows/tree/873892e7d5f5fa19737b93e01f608f52a8f65a0f/actions/test-red-discordbot-downloader), pinned at `NNTin/d-flows@873892e7d5f5fa19737b93e01f608f52a8f65a0f`. Kept in this repo so a CI incident can be reproduced and iterated on locally without waiting on upstream changes to the shared action. On top of the vendored copy, this adds the `unload_scope` input (see below) so `check-cogs.yml` can exercise both a cold-start and a warm-start dependency-loading scenario per cog.

## Overview
This composite action provisions a temporary git repository containing your cogs, adds it to Red-DiscordBot through the Downloader cog, installs the cogs using `repo add`/`cog install`, and validates them via Red's RPC interface. Unlike [`test-red-discordbot`](../test-red-discordbot) which simply copies directories into the cog path, this action runs the full downloader flow end-to-end so you can catch metadata, dependency, and git issues earlier.

## Inputs
| Name | Required | Default | Description |
| --- | --- | --- | --- |
| `token` | ✅ | – | Discord bot token passed to `redbot tinkerer --token`. |
| `cog_paths` | ✅ | – | Comma-separated list of paths to cog directories on the runner (e.g. `cogs/foo,cogs/bar`). Each directory must contain a valid `info.json`. |
| `repo_name` | ❌ | `test-repo` | Friendly label used when the downloader registers the temporary repository. Must be unique per run; characters outside Python identifiers are converted automatically. |
| `repo_url` | ❌ | `""` | When set, Downloader installs directly from this remote git URL and the action skips creating the temporary local repo. Leave empty to generate a throwaway repo from `cog_paths`. |
| `repo_branch` | ❌ | `""` | Optional branch name to checkout after cloning. Leave empty to let Downloader detect the default branch or use the local repo's branch. |
| `rpc_port` | ❌ | `6133` | Port the Red RPC server will listen on. Keep default unless you have networking conflicts. |
| `unload_scope` | ❌ | `cog` | `cog` unloads only the cog under test before its load/unload cycle (its `required_cogs` may already be loaded from an earlier cog's turn -- the warm-start case). `cog-and-dependencies` also unloads everything in its `required_cogs` (transitively) first, forcing a genuine cold-start dependency bootstrap. |

## Usage example
```yaml
name: Check Cogs (downloader path)
on:
  pull_request:
  push:
    branches: [main]

jobs:
  downloader-tests:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout repo
        uses: actions/checkout@v4

      - name: Install Red via reusable flow
        uses: ./d-flows/actions/install-red-discordbot
        with:
          python-version: '3.11'

      - name: Configure Red instance
        uses: ./d-flows/actions/setup-red-discordbot
        with:
          instance: tinkerer

      - name: Run downloader-based cog tests
        uses: ./d-flows/actions/test-red-discordbot-downloader
        with:
          token: ${{ secrets.DISCORD_BOT_TOKEN }}
          cog_paths: cogs/example,cogs/second
          repo_name: pr-${{ github.event.number || github.run_id }}
```

## How it works
1. The action installs the `aiohttp` dependency used by the RPC test client.
2. If `repo_url` is empty, the action creates a temporary working tree, copies all requested cogs into it, and commits git metadata. When `repo_url` is provided, this step is skipped and Downloader talks to your remote directly.
3. Red-DiscordBot (`redbot tinkerer ... --rpc`) starts in the background and writes logs to `${{ runner.temp }}`.
4. The helper script `test_downloader_cogs.py` loads Red's configuration, initializes the downloader `RepoManager`, and adds the temporary git repo via `repo add` semantics.
5. Downloader installs each cog into Red's configured install path, ensuring requirements are installed into Downloader's library directory.
6. Using the RPC websocket endpoint, every installed cog is loaded and then unloaded to verify that the installation truly works inside a live bot.
7. On success or failure the action cleans up: Red is stopped, installed cogs are removed, the downloader clone is deleted, and the temporary repository is discarded.

## Requirements
- Runner must already have Red-DiscordBot configured (e.g., via `install-red-discordbot` + `setup-red-discordbot` flows).
- Git 2.x and Python 3.11+ must be available on the runner image.
- Downloader relies on cog `info.json` metadata being present and valid JSON.
- Each cog must live in its own directory; provide each directory path through `cog_paths`.

## Troubleshooting
- **Missing config**: Ensure `redbot-setup` has been run for the `tinkerer` instance; otherwise the action will fail when it can't find `~/.config/Red-DiscordBot/config.json`.
- **Invalid repo name**: Downloader repo names must be valid identifiers. The action converts hyphens to underscores, but other invalid characters will raise errors.
- **Requirement installation failures**: Review the action log to see which dependency pip command failed; you may need to add wheels or pin compatible versions.
- **RPC timeout**: The helper waits 30 seconds for the websocket endpoint. Check the Red log tail dumped by the action when failures occur.
- **Git errors**: Verify each cog directory includes necessary files and can be committed. Ensure your cogs don't include very large binaries or files requiring Git LFS.
- **Remote-only installs**: When `repo_url` is set the action does not mirror local files. Make sure the specified remote repo already contains the cogs you expect to load, and keep `cog_paths` aligned with those names so the RPC validation step knows what to exercise.
- **Known limitations**: The helper assumes Downloader installs requirements with pip and the auto-generated local repo uses a `master` branch; specify `repo_branch` if your remote uses a different branch layout.
- **Custom repo branches**: Use `repo_branch` when your remote's default branch isn't detected automatically (for example `main` or feature branches).

## Comparison with `test-red-discordbot`
| Capability | `test-red-discordbot` | `test-red-discordbot-downloader` |
| --- | --- | --- |
| Installs via downloader git repo | ❌ copies directories directly | ✅ clones and installs through downloader |
| Validates `repo info.json` metadata | ❌ | ✅ |
| Installs requirements using downloader logic | ❌ (manual `uv pip install`) | ✅ (repo-managed pip install) |
| Exercises load/unload through RPC | ✅ | ✅ |
| Detects downloader-specific regressions (repo config, git hooks, metadata) | ❌ | ✅ |

Use the downloader variant when you need high confidence that your cogs can be installed by end users through `[p]repo add` and `[p]cog install`. Keep using the RPC-only version for faster smoke tests when git/dependency flows are not critical.
