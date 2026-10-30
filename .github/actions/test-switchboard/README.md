# test-switchboard

End-to-end checks for [`switchboard`](../../../switchboard), run by
[`.github/workflows/switchboard.yml`](../../workflows/switchboard.yml) once per
`SOURCE` (`addpath`, `downloader`).

`run.sh` creates a throwaway Red instance and runs `switchboard_e2e.py` in
three phases:

1. **prepare** (offline): registers the repo checkout with CogManager, which
   is what `[p]addpath` does. With `SOURCE=downloader` it also installs
   switchboard through Downloader's `RepoManager` and records it in
   Downloader's Config, which is what `[p]repo add` + `[p]cog install` leave
   behind.
2. **fresh**: starts Red and drives it over Red's RPC. On an instance's
   first start Red ignores `--load-cogs`, so this phase loads switchboard
   with `CORE__LOAD`. Then:
   - `SWITCHBOARD__STATUS` reports switchboard's own source (and, for
     `downloader`, its shadowed addpath copy). It also reports corridor as
     addpath with missing requirements.
   - `CORE__LOAD corridor` fails, because Red alone installs nothing.
   - `SWITCHBOARD__LOAD all` installs every requirement and loads all office
     cogs, dependencies first.
   - `SWITCHBOARD__UNLOAD corridor` unloads every dependent first.
   - Everything is loaded again, which leaves a managed set.
3. **restarted**: `run.sh` stops Red, empties Downloader's lib directory (a
   fresh container), and restarts. This second start does honour
   `--load-cogs switchboard`. Red's own autoload fails on corridor.
   Switchboard's autorepair must reinstall and reload every managed cog,
   checked over RPC and in the bot log.

Locally (needs a bot token, and `red-discordbot` installed in the active
Python):

```sh
DISCORD_TOKEN=... SOURCE=addpath .github/actions/test-switchboard/run.sh
```

The dashboard stub from
[`test-red-discordbot-downloader-local/fixtures`](../test-red-discordbot-downloader-local/fixtures)
is served from a `--cog-path`, so floorplan does not DM the bot owner, and
switchboard's `cli_path` classification is covered too.
