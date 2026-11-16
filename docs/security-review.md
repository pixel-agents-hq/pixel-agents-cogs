# Security and permission review

The security and functionality review against Red's
[Cog Creator guide](https://docs.discord.red/en/latest/guide_cog_creators.html)
(*No malicious code*, *No escalation of permissions*, *Respect the role
hierarchy*, *Discord TOS*, *Input validation*, *Don't expose user data to
additional audiences*). Each area lists what was checked, the result,
and a short summary for the cogboard application. It describes the code
as it is; when the code changes, update the area it touches.

## 1. Permission escalation

**Checked.** Every path an LLM agent (pico, architect, painter, animator, a
bootcamp agent) can act through:

- pico's tool set for a turn (`pico/adapters/listener.py`): `send_reply`,
  one `consult_<agent>` per registered agent, and corridor's cross-cog tool
  registry (`@llm_tool` commands and toolbox-wrapped commands);
- the agents' own native tools, the MCP tools bridged into them by
  corridor's `AgentToolServerRegistry` (suggestionbox, telephonepole), and
  A2A consultations between agents;
- every owner-only command, mention handling, role and member mutations,
  and guild scoping.

**Result.**

- **Commands run as the member who triggered the turn.** A cross-cog tool
  is offered only if `corridor.list_tools_for(ctx)` passes it for that
  `ctx.author`: its corridor permission group (`required_group`) or, with
  none, the command's own Red check pipeline (`can_run(ctx,
  check_all_parents=True)`), plus toolbox's per-server toggles. The
  handler then runs the command callback with that same `ctx`, so checks
  inside the command body (role hierarchy, channel permissions) see the
  member, not the bot.
- **No owner-only tool is reachable from a turn.** Toolbox never offers or
  wraps a command Red marks bot-owner-only (`requires.privilege_level ==
  BOT_OWNER`, on it or a parent group), even for the owner who opens the
  panel: a turn reads other members' messages too, which could steer the
  LLM while the owner is talking. A command without `can_run` is never
  wrapped. No `@llm_tool` command is owner-only.
- **No mass pings.** Corridor sends every reply, in embed and text mode,
  with `allowed_mentions=AllowedMentions.none()` (`corridor/adapters/api.py`
  `NO_MENTIONS`), independent of the bot's own default. No cog pings anyone
  on purpose.
- **No role, member or moderation actions.** No cog adds or removes roles,
  edits members, kicks, bans or bulk-deletes messages, so there is no role
  grant to compare against the member's top role. A toolbox-wrapped
  third-party command keeps its own hierarchy checks, which see the member.
- **Red's blocklist and `[p]ignore` apply.** Pico starts no turn for a
  member Red's allowlist/blocklist rejects or in an ignored channel or
  guild, and none in a server where pico is off or the cog is disabled.
- **Agent consultations are gated per member.** Pico offers a
  `consult_<agent>` tool only when the member satisfies that agent's
  `required_permission_group` (bootcamp agents); `[p]bootcamp ask` checks
  the same group and works only in a server. Architect, painter and
  animator have no group: their native tools only read and edit the
  shared editor office, which the open editor page already lets anyone
  edit, and animator's renders go to the pixel-art-mcp server the owner
  registered.
- **The A2A listener cannot be used to skip those gates.** It requires a
  per-load bearer token only in-process callers hold (section 2).
- **MCP-bridged tools have no Discord access.** Their handler gets `None`
  (or call options) as context and talks only to the MCP server the owner
  registered and granted to that agent (`[p]telephonepole agents`,
  `[p]suggestionbox agents`).
- **No cross-guild data access.** Agent tools act only on the editor
  office (`OfficeStateKind.EDITOR`), never a guild's. `deskutils quote`
  refuses a message outside the invoking server or in a channel the member
  can't read. Floorplan loads layouts only into the invoking server's
  office, and CCTV writes to a guild office need bot owner or keyholder
  access in that guild.
- **Owner-only configuration stays owner-only.** LLM endpoint/key, A2A and
  MCP listener addresses, MCP server registration, agent creation, toolbox
  tool selection, webview pins and switchboard are all `@commands.is_owner()`.
  Corridor's `owner` permission group means the bot owner or a member with
  Administrator in that server.

**Summary.** An LLM agent can only do what the member who triggered it
could do: tools are filtered per member through corridor permission groups
and Red's own command checks, and run as that member. Bot-owner-only
commands are never exposed as tools, replies never ping, and no cog manages
roles or members.

## 2. Network surfaces

**Checked.** Bind address, authentication, path handling and request size
on every listener, and what is disclosed about each.

| Listener | Default bind | Who gets in | Limits |
|---|---|---|---|
| corridor A2A (uvicorn/Starlette) | `127.0.0.1:8931` | Only requests with `Authorization: Bearer <token>`, a random token minted on each corridor load and handed only to in-process callers (`corridor.a2a_auth_headers()`); anything else gets 401 | Request body 1 MiB (413) |
| cctv office/editor (aiohttp) | `127.0.0.1:3210` | Public and unlisted offices: anyone with the link. Private offices: nothing is sent until the socket presents a one-minute, single-use, guild-bound Dashboard ticket for a member, and the `Origin` must match `[p]cctv publicurl` once set. Office writes re-check bot owner or keyholder on every write. The editor page is open to anyone, to watch and edit | Client frame 4 MiB; ticket wait 10 s; `/cctv/health` returns aggregate counts only |
| cctv static assets (Red Web Dashboard route) | Dashboard's | Anyone | Paths resolved inside the built webview only (no `..`, `\`, `:`, control characters, or symlink escape) |
| suggestionbox MCP (FastMCP) | `127.0.0.1:8934` | Served at a random path minted on each load that only corridor's registry knows; anything else gets 404 | 4 MiB body, DNS-rebinding protection on loopback (FastMCP defaults) |
| telephonepole | none (outbound only) | Connects to MCP servers the bot owner registers | 30 s discovery handshake; image/resource payloads capped at 8 MiB |

Every listener binds loopback by default, and changing that is an owner
command. All of them are disclosed in the root `README.md` and in the
owning cog's `install_msg`.

**Summary.** The cogs open three loopback-only listeners by default. The
agent (A2A) listener accepts only the bot's own cogs, via a per-load token.
The feedback MCP endpoint lives at an unguessable per-load path. The office
pages are view-only for the public, member-gated for private offices, and
write-gated by role. Every request and frame size is bounded.

## 3. Data exposure

**Checked.** What each cog sends to the LLM endpoint, A2A agents and MCP
servers; what the office pages show and to whom; and whether each
`end_user_data_statement`, `install_msg` and the README say the same.

**Result.**

- **LLM endpoint** (`[p]corridor llm endpoint`, default
  `https://litellm.nntin.xyz/`; no request until the owner sets a key).
  Pico sends the triggering message and the ten messages before it in that
  channel (content and author display names), only in servers where it is
  enabled. Agents send the A2A request text, the editor layout, tool
  results and (animator) rendered images. Bootcamp sends `[p]bootcamp ask`
  prompts.
- **A2A agents.** They receive the request pico's LLM writes, inside the
  bot process.
- **MCP servers.** They receive the tool arguments the agent chooses.
  These can quote conversation content, and go only to servers the owner
  registered and granted to that agent.
- **Office pages.** An enabled server's office shows members' display
  names, characters, presence, rich presence and message text, to anyone
  (public, the default after `[p]cctv enable`), anyone with the link
  (unlisted) or signed-in members (private). Message text comes only from
  channels `@everyone` can read, never a restricted channel or private
  thread: corridor's gateway publishes nothing from other channels, and a
  turn asked for from one is private (`activity_guild(..., private=True)`,
  carried to consulted agents in A2A metadata), so corridor drops its
  `AgentReplied`/`AgentToolStarted` events. The editor page shows the
  agents' activity under the same rule. Server managers can hide offline
  members and rich presence.
- **Stored user data.** It is listed in
  [`architecture.md#end-user-data`](architecture.md#end-user-data), and
  `red_delete_data_for_user` deletes all of it. The one exception is the
  office seat records left in the old Config identities on an install that
  ran the cogs before the office refactor; no cog reads them, and they are
  not deleted.
- **Statements match.** `contracts/info_json/lint_info_json.py` checks
  every cog's statements and install message exist and name its
  dependencies, and each cog's `test_user_data.py` checks that Red serves
  the `info.json` statement. The statements, install messages and README
  "What leaves the bot" table name the same destinations listed above.

**Summary.** Message content goes only to the bot owner's configured LLM
endpoint and the MCP servers the owner chose, and only where the owner
turned an agent on. The office pages show live presence and messages only
from channels everyone in the server can read, with per-server visibility.
Nothing is persisted beyond office characters and seats, which a data
deletion request removes.

## 4. Input validation

**Checked.** Every place user, LLM or remote input reaches a file path, a
subprocess, a URL or a query.

**Result.**

- **File paths.** Avatar files are named by a SHA-256 of the seed code
  (`pixelagents/application/characters.py` `avatar_filename`). CCTV's
  static route rejects `..`, empty segments, `\`, `:` and control
  characters, and checks that the resolved path stays inside the build.
  Cogs write only under `cog_data_path`.
- **Subprocesses.** `git`, `npm`, `node` and `pip` run with argument lists
  (no shell). The webview and pixatar commits come from pinned files or an
  owner command validated as a 7-40 character hex hash. `pip` installs the
  requirements in installed cogs' `info.json`, as Downloader does.
- **URLs and SSRF.**
  - Owner-set URLs (LLM endpoint, Pixel Index API/web, MCP servers, CCTV
    public URL) are owner-only commands, and floorplan's are checked to
    be http(s).
  - Member and LLM input reaches URLs only as quoted path segments (a
    Pixel Index slug) or encoded query parameters.
  - Animator downloads a render artifact only from the same scheme, host
    and port as the MCP server its `get_job` came from
    (`RegisteredTool.server_url`), without following redirects, capped at
    25 MiB.
  - A message link resolves through Discord's API only.
- **Queries.** There is no SQL. Config access uses fixed keys and
  integer IDs. CCTV parses socket messages with strict pydantic models
  (`cctv/contracts/websocket.py`), and guild IDs in socket paths must be
  decimal and at most 20 digits.

**Summary.** User and LLM input never reaches a shell, a file path or an
arbitrary URL. Subprocesses take argument lists from pinned or validated
values, file names are hashed or confined to the cog's own directories,
and the one tool that downloads from a URL it was handed only fetches from
the owner-registered server that handed it.

## 5. Licensing

**Checked.** Third-party code and assets in the repository and in what the
cogs build and serve, and their attribution.

**Result.**

- **The repository** is MIT (`LICENSE`).
- **Pixel Agents** (MIT, Pablo De Lucca) is not committed. Pixelagents
  clones it on the bot host at the pinned commit and builds it, and every
  build carries upstream's `LICENSE` as `LICENSE.txt` beside `index.html`.
  CCTV serves that file with the bundle, and a checkout without a license
  is not built. The bundle includes upstream's FS Pixel Sans font, which
  upstream ships without a separate license file. Its character art is
  based on JIK-A-4's free Metro City pack, credited in the root README.
- **The pixatar CLI** comes from Pixel Index (MIT). It is cloned and built
  on the host and only runs there.
- **`emit_decoded_assets.ts`** imports upstream's own decoders from the
  checkout rather than copying them.
- **Switchboard** calls Red's load/unload code rather than copying it.
- **The cogs' `avatar.png` assets** are the project's own.
- **Credits.** The root README's Credits section names Pixel Agents,
  Metro City, Pixel Index, pixel-art-mcp and Red-DiscordBot.

**Summary.** The repository is MIT and contains no copied third-party
code. The Pixel Agents webview (MIT) is built from upstream on the bot
host and served with its license file, and every upstream project and art
source is credited in the README.
