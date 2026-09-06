# animator

A2A-only LLM agent that models and renders pixel-art sprites and animations
via [pixel-art-mcp](https://github.com/NNTin/pixel-art-mcp), and delivers the
resulting Discord-ready assets (`pixel-agents.zip`, `preview.png`) as real
message attachments -- never just a `download_url` in text, which a Discord
user has no way to reach (see `tools/deliver_assets_tool.py`'s module
docstring).

Like [`architect`](../architect) and [`painter`](../painter), animator is
never Discord-user-facing directly: [`pico`](../pico) (or any future
Discord-facing agent) consults it over A2A, mounted on corridor's shared
listener. Unlike architect/painter, animator has almost no native tool
logic of its own -- its entire capability set is bridged live from
`pixel-art-mcp` via corridor's `AgentToolServerRegistry`, the same
mechanism [`suggestionbox`](../suggestionbox) and
[`telephonepole`](../telephonepole) already use for other MCP servers. Its
only native tool, `deliver_pixel_agents_assets`, exists purely to fetch a
finished job's output files and stage them as real attachments -- see
"Architecture" below.

## Installing

Requires [`corridor`](../corridor) (auto-loaded on `cog_load` via
`dependency_loader.ensure_corridor_loaded()` -- `required_cogs` is only a
Downloader install hint):

```
[p]repo add pixel-agents-cogs https://github.com/pixel-agents-hq/pixel-agents-cogs
[p]cog install pixel-agents-cogs animator
[p]load animator
```

### Required setup: registering pixel-art-mcp

Animator has no tools of its own until `pixel-art-mcp` is registered and
enabled for it via [`telephonepole`](../telephonepole) (also requires
`[p]load telephonepole`):

```
[p]telephonepole add pixel-art http://pixel-art-mcp:8000/mcp
[p]telephonepole agents pixel-art
```

The second command opens a Components V2 panel -- toggle `animator` on.
`http://pixel-art-mcp:8000/mcp` assumes redstack and `pixel-art-mcp` share
the `redstack-network` Docker network (see
`projects/nntin-labs/services/pixel-art-mcp-deploy/README.md` in the
`lair.nntin.xyz` monorepo for that deployment).

Also configure corridor's shared LLM (`[p]corridor llm endpoint/key/model`)
and A2A listener (`[p]corridor a2a host/port`) if not already done for
architect/painter/pico.

## Commands

Every command is bot-owner scoped -- animator is A2A-only and
process-scoped, with no per-guild state of its own.

| Command | Description |
|---|---|
| `[p]animator maxtoolcalls <count>` | Set the max tool calls animator may make per A2A turn |
| `[p]animator requesttimeout <seconds\|default>` | Override the LLM request timeout for animator's tool loop, or reset to corridor's shared default |
| `[p]animator debuglogging <true\|false>` | Enable/disable verbose per-tool-call logging |
| `[p]animator prompt set <text>` | Set animator's system prompt |
| `[p]animator prompt reset` | Reset the system prompt to the default |
| `[p]animator prompt show` | Show the current system prompt |
| `[p]animator status` | Show LLM/A2A/pixel-art-mcp registration status |

pixel-art-mcp's tool calls (Blender scripting, rendering) can run much
longer than a typical chat completion, so corridor's shared default LLM
request timeout may be too short for animator specifically -- if you see
`LiteLLM request timed out` in the logs for `red.animator`, raise it with
`[p]animator requesttimeout <seconds>` (e.g. `[p]animator requesttimeout
180`). `[p]animator requesttimeout default` resets to corridor's own
shared default.

## Architecture

Animator's tool list, rebuilt fresh every A2A turn (so a `[p]telephonepole
agents` toggle takes effect immediately, no reload needed):

1. Whatever `pixel-art-mcp` tools are currently enabled for `animator`
   (`create_project`, `execute_blender_python`, `inspect_scene`,
   `render_preview`, `render_sprites`, `get_job`, `cancel_job`,
   `get_artifact`, ...) -- bridged via `tools/agent_tool_server.py`'s
   `AgentToolServerTool`, adapting each `corridor.domain.RegisteredTool`
   into animator's own `ToolSpec`.
2. `deliver_pixel_agents_assets` (`tools/deliver_assets_tool.py`) --
   animator's one native tool.

### Why a dedicated delivery tool

`pixel-art-mcp`'s `get_job` result lists a finished job's output artifacts
as `{filename, media_type, download_url}`, where `download_url` is an
internal-network address (`http://pixel-art-mcp:8000/artifacts/...`) --
reachable from any container on `redstack-network`, but not from wherever a
Discord user is. Left as plain text, that URL is not a usable answer.

`deliver_pixel_agents_assets(job_id)`:

1. Looks up the job via the same bridged `get_job` `RegisteredTool` (so it
   respects whatever per-agent enable/connection settings
   `[p]telephonepole` has configured -- no separate MCP connection of its
   own).
2. Downloads the bytes of the `pixel-agents.zip` and `preview.png`
   artifacts directly (a plain HTTP GET, not an MCP call -- fetching a file
   isn't a tool invocation).
3. Returns a small `status`/`message` result to the LLM (so the model
   knows delivery succeeded), while the actual bytes travel out-of-band as
   `Attachment`s -- see below.

### How bytes reach Discord without bloating the LLM's own context

A tool's `Output` is normally serialized with `model_dump_json()` and fed
straight back into the LLM's chat history. Raw file bytes have no business
being fed to a language model as text, so they never go through that path:

```
DeliverPixelAgentsAssetsTool.Output.attachments   (Field(exclude=True) -- never serialized to the LLM)
  -> application/tool_loop_service.py: ToolLoopService accumulates them
       into ToolLoopResult.attachments (an extension over architect's/
       painter's identical loop shape)
  -> corridor.domain.agent_executor.GenericAgentExecutor._run_turn reads
       `getattr(result, "attachments", ())` and appends one
       a2a.types.Part(raw=..., filename=..., media_type=...) per
       attachment to the final A2A message, alongside the text Part
  -> pico/infrastructure/architect_client.py's ArchitectClient.ask()
       collects any Part with a non-empty `raw` back out as its own
       Attachment
  -> pico/tools/consult_agent_tool.py's ConsultAgentTool builds a
       discord.File per attachment and passes them through
       corridor.adapters.reply_sender.ReplySender.send_reply(...,
       extra_files=...) -- landing as real Discord attachments on the
       same message that announces animator's answer.
```

Every step in that chain is additive/backward-compatible: `getattr(...,
())` means architect/painter/pico/bootcamp's own `ToolLoopResult` (which
have no `attachments` field at all) are unaffected, and `extra_files`
defaults to empty everywhere it was added.

## Docs

See [`docs/corridor.md`](../docs/corridor.md) for how `required_cogs` and
corridor's dependency-loading work in general, and
[`docs/agent-directory-design.md`](../docs/agent-directory-design.md) for
how a registered A2A agent (architect, painter, animator, ...) is mounted
on corridor's one shared listener.
