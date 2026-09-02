# toolbox

## Overview

`toolbox` is a bot-owner host/configuration cog with two independent
surfaces:

- **Node.js/npm installer.** Downloads an official Node.js prebuilt release
  from nodejs.org into Red's per-cog data directory and puts it on `PATH`,
  so the bot host has `node`/`npm` available without shell access to the
  machine. Defaults to the latest Node.js 22.x LTS release — the version
  pinned across this ecosystem (pixel-index's `engines.node`, pixel-agents'
  `.nvmrc`) — useful alongside `pixelagents`, whose webview build needs
  `node`/`npm` present on the same host.

- **LLM tool toggle panel.** `corridor` hosts a cross-cog registry of tools
  an LLM (via `pico`) can call; a cog opts one of its own commands in at
  authoring time with `@llm_tool()`. `[p]toolbox tools` is the runtime
  complement: it lets the bot owner pick from *any* command listed in
  `[p]help` — including ones nobody decorated — and turn it into a tool,
  without touching that command's code. `[p]toolbox tools guild` lets a
  guild admin override a registered tool's visibility for their server
  only, on top of the owner's global default. See
  [`docs/toolbox-command-tool-toggle-design.md`](../docs/toolbox-command-tool-toggle-design.md)
  for the full design.

## Commands

| Command | Permission | Description |
|---|---|---|
| `[p]toolbox node install [version]` | Bot owner | Install Node.js/npm (latest 22.x LTS if `version` omitted) |
| `[p]toolbox node uninstall` | Bot owner | Remove the installed Node.js/npm |
| `[p]toolbox node version` (alias `status`) | Bot owner | Show what's installed |
| `[p]toolbox tools` | Bot owner | Open the global LLM tool selection panel |
| `[p]toolbox tools guild` | Guild admin (`manage_guild` or Administrator) | Open the per-guild tool visibility override panel |

`[p]toolbox tools guild` is deliberately gated on guild-admin permissions
rather than corridor's per-guild permission tiers (`require_permission`):
this is bot-configuration surface, not a general utility, so it follows
Discord's own guild-management permission instead. Every other command is
bot-owner only (`@commands.is_owner()`), since installing Node.js or
setting the global tool-visibility default affects every guild the bot
serves, not just the one the command was run from.

## Configuration

Requires [`corridor`](../corridor), auto-loaded on `cog_load` via
`dependency_loader.ensure_corridor_loaded()` — `required_cogs` in
`info.json` is only a Downloader install hint, not something Red loads on
its own:

```
[p]repo add pixel-agents-cogs https://github.com/pixel-agents-hq/pixel-agents-cogs
[p]cog install pixel-agents-cogs toolbox
[p]load toolbox
```

All persistent state lives in Red Config under one identifier, split
across three repository classes: `RedNodeRepository` (`installed_version`,
`installed_dir`, global), `RedToolSelectionRepository`
(`selected_tool_commands`, global — which commands the owner has opted
into tool-wrapping), and `RedToolVisibilityRepository`
(`tool_enabled_default`, global, plus `tool_enabled_override`, per guild —
whether a selected or already-decorated tool is currently visible). See
[`docs/toolbox-command-tool-toggle-design.md`](../docs/toolbox-command-tool-toggle-design.md#domain-modelschema)
for the exact schema and how the two toggle layers combine.

## Related docs

- [`docs/toolbox-command-tool-toggle-design.md`](../docs/toolbox-command-tool-toggle-design.md)
  — full design of the tool selection/visibility feature: corridor's
  visibility filter hook, the Config schema, and the panel UI.
- [`docs/corridor.md`](../docs/corridor.md) — `required_cogs` and
  corridor's dependency-loading model in general; toolbox uses the same
  `ensure_corridor_loaded()` pattern as every other cog here even though
  its own commands don't call into corridor's permission checks.
