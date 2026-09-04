# bootcamp

Dynamically create custom LLM agents with their own system prompt, registered as A2A agents.

Lets a bot owner create/remove custom LLM agents at runtime, each with its own system prompt and corridor permission-group gate. Every created agent registers into corridor's shared `AgentDirectoryService` (making it consultable by pico's dynamic `consult_<agent_key>` tool, and visible on cctv, exactly like architect/painter), gets whatever MCP tools are currently granted to it via corridor's `AgentToolServerRegistry` (suggestionbox/telephonepole), and can also be invoked directly with `[p]bootcamp ask <agent_key> <prompt>`.

See [`docs/bootcamp-design.md`](../docs/bootcamp-design.md) for the full design.

## Installing

Requires [`corridor`](../corridor) (auto-loaded on `cog_load` via `dependency_loader.ensure_corridor_loaded()` -- `required_cogs` is only a Downloader install hint):

```
[p]repo add pixel-agents-cogs https://github.com/pixel-agents-hq/pixel-agents-cogs
[p]cog install pixel-agents-cogs bootcamp
[p]load bootcamp
```

Corridor's shared LLM connection must be configured (`[p]corridor llm endpoint/key/model`, bot owner) before any custom agent can produce an answer.

## Commands

| Command | Gate | Description |
|---|---|---|
| `[p]bootcamp create` | bot owner | Open a Components V2 panel to create a custom agent -- key, system prompt, description, max tool calls, and request timeout in one modal, then who may use it right after |
| `[p]bootcamp remove <agent_key>` | bot owner | Remove a custom agent |
| `[p]bootcamp list` | bot owner | List every custom agent and its settings |
| `[p]bootcamp permission <agent_key> <group_key>` | bot owner | Set which corridor permission group gates use of an agent |
| `[p]bootcamp maxtoolcalls <agent_key> <value>` | bot owner | Set an agent's per-turn tool-call budget |
| `[p]bootcamp debuglogging <agent_key> <true\|false>` | bot owner | Toggle an agent's debug-event streaming |
| `[p]bootcamp requesttimeout <agent_key> <seconds\|default>` | bot owner | Override an agent's LLM request timeout, or reset it to corridor's own default |
| `[p]bootcamp description <agent_key> <text\|default>` | bot owner | Set an agent's `AgentCard` description -- the text pico's LLM sees when deciding whether to consult it |
| `[p]bootcamp ask <agent_key> <prompt...>` | that agent's own `permission_group` | Directly consult a custom agent |

`agent_key` must start with a lowercase letter and contain only lowercase
letters, digits, and underscores (it doubles as the agent's display name
and pico's `consult_<agent_key>` tool suffix), and cannot be one of the
reserved subcommand names above.

## Docs

See [`docs/bootcamp-design.md`](../docs/bootcamp-design.md) for the
architecture, domain model, and key flows, and
[`docs/corridor.md`](../docs/corridor.md) for how `required_cogs` and
corridor's dependency-loading work in general.
