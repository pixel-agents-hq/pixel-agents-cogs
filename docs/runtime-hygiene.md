# Runtime hygiene

How every cog here meets the runtime recommendations of Red's
[Approved Cog Creator guide](https://docs.discord.red/en/latest/guide_cog_creators.html)
(*Safety and stability*, *Logging*, *Tasks*, *Events*, *Botwide features*,
*i18n*). `contracts/runtime_hygiene/lint_runtime_hygiene.py` enforces the
two rules that can be checked statically (logger names, blocking calls in
coroutines) in CI via `.github/workflows/runtime-hygiene.yml`:

```sh
python -m contracts.runtime_hygiene.lint_runtime_hygiene
python -m unittest discover -s contracts/runtime_hygiene/tests -t .
```

## Logging

Every logger lives under one namespace, `red.pixel-agents-cogs`:

| Logger | Used for |
|--------|----------|
| `red.pixel-agents-cogs.<cog>` | a cog's own module-level `log` |
| `red.pixel-agents-cogs.<cog>.<sub>` | a named child, e.g. `red.pixel-agents-cogs.switchboard.pip` |
| `f"red.pixel-agents-cogs.{__name__}"` | a per-module default logger; Red imports each cog as a top-level package, so `__name__` is `<cog>.<module path>` and this is a child of the cog's logger |

So `[p]set logging`-style filtering, or a handler on
`red.pixel-agents-cogs`, catches the whole repo, and a handler on
`red.pixel-agents-cogs.<cog>` catches one cog including its submodules.
The cookiecutter template's `adapters/cog_base.py` starts every new cog
with `log = logging.getLogger("red.pixel-agents-cogs.<cog>")`. Tests may
name throwaway loggers freely; the lint skips `tests/` and `conftest.py`.

## Background tasks

Every task a cog starts is owned by something that cancels it in
`cog_unload` and logs its failure:

| Cog | Task | Cancelled by | Failure logged by |
|-----|------|--------------|-------------------|
| `corridor` | uvicorn `serve()` for the A2A listener | `A2AServer.stop()` from `cog_unload`: asks the server to exit, waits `STOP_GRACE_SECONDS`, then cancels | a done-callback on the task, the moment it ends with an exception |
| `suggestionbox` | uvicorn `serve()` for the MCP listener | `McpListener.stop()` from `cog_unload`, same drain-then-cancel | the same done-callback pattern |
| `cctv` | initial sync, private-viewer sweep, bubble-clear timers | `TaskSupervisor.shutdown()` from `cog_unload` | `TaskSupervisor._done` |
| `switchboard` | `_repair_when_ready` | `cog_unload` | `log.exception` inside the task |

corridor's event bus starts no task: `EventBusService.publish` awaits each
subscriber with a timeout and logs a raising or hung one.

## No blocking on the event loop

Long or unbounded work never runs on the event loop:

- **Subprocesses**: the webview and pixatar builds (`npm`, `git`) run in
  `asyncio.to_thread` (`pixelagents/adapters/cog_base.py`); switchboard's
  `pip install` uses `asyncio.create_subprocess_exec`.
- **Node install** (download, extract, activate) runs in
  `asyncio.to_thread` (`toolbox/application/service.py`).
- **Network**: aiohttp (`floorplan`, corridor's LLM client) or async MCP and
  HTTP clients; toolbox's `urllib` download runs in a worker thread.
- **File IO**: cctv's dashboard pages read `index.html` and static assets,
  and decode the webview's sprite JSON (`WebviewAssets.refresh`), in
  `asyncio.to_thread`. switchboard's cog-path scan and its read of every
  installed distribution's metadata do the same. pixelagents writes
  character PNGs in a worker thread.

Small one-off reads stay inline: a cog's own `info.json` at import, and
pixelagents' sync cross-cog accessors for small generated JSON files.

The lint fails on a blocking call (subprocess, `time.sleep`, `urlopen`,
`requests`, bulk `shutil`, `Path.read_text`/`write_text`/`read_bytes`/
`write_bytes`) written directly inside an `async def`. Move such work into
a sync helper and `await asyncio.to_thread(helper, ...)`.

## Guild-facing listeners and bus subscribers

| Path | Early exits, in order |
|------|-----------------------|
| corridor `on_member_update` / `on_presence_update` | no relevant change; Corridor disabled in the guild |
| corridor `on_member_join` / `on_member_remove` | Corridor disabled in the guild |
| corridor `on_message` | DM; this bot's own message; Corridor disabled in the guild. Other bots' messages are published on purpose: offices render bot members, and each subscriber applies its own `include_bots` setting |
| pico `on_message` | bot author; DM; Pico disabled in the guild; Pico not enabled for the guild (Config); LLM not configured |
| cctv bus subscribers (`AgentPresenceChanged`, `AgentReplied`, highlights, tool and status events) | skip the office of a guild CCTV is disabled in. Closing a departed member's private-office socket still happens: that is access control, not activity |

"Disabled in the guild" is `await bot.cog_disabled_in_guild(cog, guild)`,
set by a server admin with `[p]command disablecog <Cog>`. The other
listeners (`on_cog_add`, `on_cog_remove`, `on_dashboard_cog_add`) are
bot-wide lifecycle events, not guild-facing.

## Botwide features

- **Embed colour**: every reply goes through corridor's renderer
  (`docs/embed-colors.md`). An embed with a `category` uses that category's
  color; one without uses Red's configured embed colour, resolved by
  corridor with `ctx.embed_color()` (or `bot.get_embed_colour(channel)`
  for a ctx-less channel reply).
- **`bot.is_automod_immune`**: not used. No cog here moderates or acts
  against members (deleting, muting, filtering), which is what that check
  exists for.

## i18n

No cog uses `Translator`/`cog_i18n`, so no event or task needs
`set_contextual_locales_from_guild`. A cog that adds translations must
call it at the start of every listener and background task that renders
translated text.
