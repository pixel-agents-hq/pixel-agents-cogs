# toolbox

Two independent bot-owner tooling surfaces: Node.js/npm management on the
bot host, and a Components v2 panel for turning any `[p]help`-listed
command into an LLM tool.

## Node.js/npm

`toolbox` downloads official Node.js prebuilt releases from nodejs.org into
Red's per-cog data directory and puts them on `PATH`, so a bot owner can
install, uninstall, or switch the Node.js/npm version their host uses
without shell access to the machine. It defaults to the latest Node.js 22.x
LTS release, the version pinned across this ecosystem (pixel-index's
`engines.node`, pixel-agents' `.nvmrc`) — useful alongside `pixelagents`,
whose webview build needs `node`/`npm` present on the same host.

## LLM tool toggle panel

`corridor` hosts a cross-cog registry of tools an LLM (via `pico`) can
call; a cog opts one of its own commands in at authoring time with
`@llm_tool()`. `[p]toolbox tools` is the runtime complement: it lets the
bot owner pick from *any* command listed in `[p]help` — including ones
nobody decorated — and turn it into a tool, without touching that
command's code. `[p]toolbox tools guild` lets a guild admin override a
registered tool's visibility for their server only, on top of the owner's
global default. See
[`docs/toolbox-command-tool-toggle-design.md`](../docs/toolbox-command-tool-toggle-design.md)
for the full design.

All commands are bot-owner only (`@commands.is_owner()`) except
`[p]toolbox tools guild`, which is guild-admin gated
(`manage_guild` or Administrator) — not corridor's per-guild permission
tiers, since this is bot-configuration surface rather than a general
utility.

## Installing

Requires [`corridor`](../corridor) (auto-loaded via `required_cogs`):

```
[p]repo add pixel-agents-cogs https://github.com/pixel-agents-hq/pixel-agents-cogs
[p]cog install pixel-agents-cogs toolbox
[p]load toolbox
```

## Commands

| Command | Description |
|---|---|
| `[p]toolbox node install` | Install Node.js/npm |
| `[p]toolbox node uninstall` | Remove the installed Node.js/npm |
| `[p]toolbox node version` (alias `status`) | Check what's installed |
| `[p]toolbox tools` | Open the global LLM tool selection panel |
| `[p]toolbox tools guild` | Open the per-guild tool visibility override panel |

## Docs

See [`docs/corridor.md`](../docs/corridor.md) for how `required_cogs` and
corridor's dependency-loading work in general — toolbox uses the same
`ensure_corridor_loaded()` pattern as every other cog here even though its
own commands don't call into corridor's permission checks.
