# {{cookiecutter.cog_name}}

{{cookiecutter.short}}

{{cookiecutter.description}}

<!-- TODO: replace the paragraph above with a real description once the cog
does more than the scaffolded CounterService example -- what it's for, who
uses it, and how it fits alongside the other cogs in this repo. -->

## Installing

Requires [`corridor`](../corridor) (auto-loaded via `required_cogs`):

```
[p]repo add pixel-agents-cogs https://github.com/pixel-agents-hq/pixel-agents-cogs
[p]cog install pixel-agents-cogs {{cookiecutter.cog_name}}
[p]load {{cookiecutter.cog_name}}
```

<!-- TODO: document any configuration steps a user needs before the cog is
useful (settings to set, roles to assign via [p]corridorsettings, other
cogs it depends on beyond corridor, ...). Delete this section if there's
truly nothing to configure. -->

## Commands

| Command | Description |
|---|---|
| `[p]{{cookiecutter.cog_name}} count` | Show this server's current count |
| `[p]{{cookiecutter.cog_name}} bump` | Increment this server's count by one (requires the keyholder tier) |
| `[p]{{cookiecutter.cog_name}} project <amount>` | Project the count after 1-10 increments without changing it |
| `[p]{{cookiecutter.cog_name}} report [compact\|detailed]` | Report the count in one of two supported styles |

<!-- TODO: this table describes the scaffolded CounterService example --
replace it with the cog's real command surface as it grows. Keep it in
sync with adapters/commands.py; this is what a user reads before digging
into the code. -->

`bump`, `project`, and `report` carry `@corridor.domain.llm_tool(...)`
directly (see `adapters/commands.py`), so if [`pico`](../pico) is installed,
loaded, and enabled for a guild, its LLM can call the same callbacks a
human invokes. Together they demonstrate a no-input tool, bounded numeric
input, a string enum, explicit permission/runtime checks, Discord replies,
and informational mapping results returned to the LLM. Delete the
decorator from a command that should not be LLM-callable; tools are opt-in
per command. `ToolDescription` shapes the advertised JSON Schema but does
not replace callback validation because tool calls bypass discord.py's
argument conversion. See
[`docs/corridor-tool-registry-design.md`](../docs/corridor-tool-registry-design.md)
for the full design.

## Docs

<!-- TODO: once this cog is more than a scaffold, add an Architecture.md
describing its layer boundaries, resource ownership, and any
boundary-enforcement tests (see corridor/Architecture.md, pixelagents's, or
floorplan's for the expected shape), and link it here. If the cog owns a
permission model beyond corridor's tiers, add a PERMISSIONS.md too. -->

See [`docs/corridor.md`](../docs/corridor.md) for how `required_cogs` and
corridor's dependency-loading work in general.
