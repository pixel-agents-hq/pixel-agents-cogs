# telephonepole

Dynamically registers third-party MCP servers for registered A2A agents.

Lets a bot owner register/unregister external MCP tools servers at runtime, making their tools available to a registered A2A agent's own tool loop (`architect`, `painter`), gated per server and per agent -- the same corridor `AgentToolServerRegistry` pattern [`suggestionbox`](../suggestionbox) uses for its own in-process server, generalized to any third-party MCP endpoint reachable at a Streamable HTTP `base_url`.

## Installing

Requires [`corridor`](../corridor) (auto-loaded on `cog_load` via `dependency_loader.ensure_corridor_loaded()` -- `required_cogs` is only a Downloader install hint):

```
[p]repo add pixel-agents-cogs https://github.com/pixel-agents-hq/pixel-agents-cogs
[p]cog install pixel-agents-cogs telephonepole
[p]load telephonepole
```

Nothing else to configure up front -- add a server with `[p]telephonepole add`, then grant access with `[p]telephonepole agents`.

## Commands

All bot-owner-only -- this is bot-wide capability configuration, not guild content.

| Command | Description |
|---|---|
| `[p]telephonepole add <name> <base_url>` | Register a third-party MCP server's Streamable HTTP endpoint under `name` (e.g. `[p]telephonepole add freecad http://freecad-mcp:8766/mcp`) |
| `[p]telephonepole remove <name>` | Unregister a previously-added server |
| `[p]telephonepole list` | List every registered server and its `base_url` |
| `[p]telephonepole agents <name>` | Open a Components V2 panel to toggle, per registered A2A agent, whether it may use that server's tools |

`add` connects to `base_url` immediately (via corridor's `AgentToolServerRegistry.register`) and only persists the entry if that succeeds -- a name already in use, an unreachable server, or a `base_url` already registered by a different cog under a different owner all come back as a Discord-visible error instead of a silent no-op. Every server is off (no agent may use it) until toggled on via `agents`, same "off by default" rule suggestionbox's own per-agent toggle uses.

On `cog_load`, every persisted server is re-registered with corridor (its in-memory registry does not survive a bot restart even though this cog's own Config does); a server that fails to re-register stays in this cog's Config so `[p]telephonepole list` still shows it, and the bot owner is notified by DM.

## Docs

See [`docs/telephonepole-design.md`](../docs/telephonepole-design.md) for the full design -- architecture, key flows, and error handling. See [`docs/corridor.md`](../docs/corridor.md) for how `required_cogs` and corridor's dependency-loading work in general, and [`corridor/application/agent_tool_server_registry.py`](../corridor/application/agent_tool_server_registry.py) for the registry this cog is a consumer of.
