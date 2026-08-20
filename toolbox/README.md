# toolbox

Tooling installation for the bot owner — currently Node.js/npm management on
the bot host.

`toolbox` downloads official Node.js prebuilt releases from nodejs.org into
Red's per-cog data directory and puts them on `PATH`, so a bot owner can
install, uninstall, or switch the Node.js/npm version their host uses
without shell access to the machine. It defaults to the latest Node.js 22.x
LTS release, the version pinned across this ecosystem (pixel-index's
`engines.node`, pixel-agents' `.nvmrc`) — useful alongside `pixelagents`,
whose webview build needs `node`/`npm` present on the same host.

All commands are bot-owner only (`@commands.is_owner()`), not gated by
corridor's per-guild permission tiers.

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

## Docs

See [`docs/corridor.md`](../docs/corridor.md) for how `required_cogs` and
corridor's dependency-loading work in general — toolbox uses the same
`ensure_corridor_loaded()` pattern as every other cog here even though its
own commands don't call into corridor's permission checks.
