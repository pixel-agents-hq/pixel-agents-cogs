# suggestionbox

MCP feedback server for reporting errors/improvements, per-agent gated.

Runs an MCP ([Model Context Protocol](https://modelcontextprotocol.io/))
tools server exposing two tools -- `report_error` and
`suggest_improvement` -- that post to a bot-owner-configured Discord
channel. Two kinds of caller reach these tools: a genuinely external MCP
client (a coding-agent CLI, an IDE integration) connects to
suggestionbox's own MCP endpoint directly; a registered A2A agent in
corridor's `AgentDirectoryService` (`architect` today, more later) reaches
the same tools from its own in-process tool-calling loop, mediated
entirely by corridor's `AgentToolServerRegistry` + MCP client. See
[`docs/suggestionbox-design.md`](../docs/suggestionbox-design.md) for the
full design.

## Installing

Requires [`corridor`](../corridor) (auto-loaded via `required_cogs`):

```
[p]repo add pixel-agents-cogs https://github.com/pixel-agents-hq/pixel-agents-cogs
[p]cog install pixel-agents-cogs suggestionbox
[p]load suggestionbox
```

## Configuring

All of this cog's own state is global (bot-wide), not per-guild -- neither
an external MCP client nor a registered A2A agent's call carries guild
context (see the design doc §2/§3 for why).

1. `[p]suggestionbox channel <#channel>` (bot owner) -- set the one
   Discord channel `report_error`/`suggest_improvement` post to.
2. `[p]suggestionbox mcp host <host>` / `[p]suggestionbox mcp port <port>`
   (bot owner) -- configure and restart this cog's own MCP listener
   (`127.0.0.1:8934` by default). Add a reverse-proxy rule if it needs to
   be reachable from outside the bot's own host.
3. `[p]suggestionbox agents` (bot owner) -- open the Components v2 panel
   and enable MCP tool access for whichever registered A2A agents should
   have it. Off by default for a newly-registered agent.

## Commands

| Command | Description |
|---|---|
| `[p]suggestionbox channel <#channel>` | Set the feedback channel (bot owner) |
| `[p]suggestionbox mcp host <host>` | Set the MCP listener's bind host and restart it (bot owner) |
| `[p]suggestionbox mcp port <port>` | Set the MCP listener's bind port and restart it (bot owner) |
| `[p]suggestionbox agents` | Open the per-agent MCP access panel (bot owner) |

## MCP tools

| Tool | Fields |
|---|---|
| `report_error` | `source`, `what_happened`, `expected`, `actual`, `severity` (low/medium/high) |
| `suggest_improvement` | `source`, `area`, `observation`, `suggestion` |

Both post a message to the configured feedback channel and return a small
`{"status": "ok" | "error", ...}` mapping to the caller. `source` is free
text identifying the reporter (`"architect"`, or a description of an
external tool/session) -- neither transport carries a stronger caller
identity.

## Docs

See [`docs/suggestionbox-design.md`](../docs/suggestionbox-design.md) for
the full design: why corridor gained a new `AgentToolServerRegistry` and
MCP client rather than reusing `ToolRegistryService`, the ctx-less
`render_channel_reply`/`send_channel_reply` primitives corridor gained for
this cog's proactive channel posts, and how architect's own tool loop
consults `corridor.list_agent_tools_for("architect")` fresh every A2A
turn. See [`docs/corridor.md`](../docs/corridor.md) for how `required_cogs`
and corridor's dependency-loading work in general.
