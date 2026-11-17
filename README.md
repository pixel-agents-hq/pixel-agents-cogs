# pixel-agents-cogs

[Red-DiscordBot](https://github.com/Cog-Creators/Red-DiscordBot) cogs that
bring [Pixel Agents](https://github.com/pixel-agents-hq/pixel-agents) to
Discord: a live pixel-art office for each server, LLM agents that chat in
Discord and edit the office, and the shared services they run on.

## Cogs

| Cog | What it does |
|---|---|
| [`corridor`](corridor) | Shared infrastructure every other cog needs (except `switchboard`): permissions, reply style, the LLM connection, the A2A listener, the MCP bridge, the event bus and office-state storage. Hidden; loaded automatically. |
| [`pixelagents`](pixelagents) | Builds the Pixel Agents webview and the pixatar character CLI, and validates every office-state read and write. |
| [`cctv`](cctv) | Serves each server's live office, and the agents' editor office, as Red Web Dashboard pages over its own HTTP/WebSocket listener. |
| [`floorplan`](floorplan) | Browses the Pixel Index layout catalogue and loads a layout into a server's office. |
| [`pico`](pico) | An LLM presence in Discord that decides whether to react to a message and acts only through tools, consulting the other agents over A2A. |
| [`architect`](architect) | An A2A-only LLM agent that changes the editor office's structure. |
| [`painter`](painter) | An A2A-only LLM agent that recolors the editor office. |
| [`animator`](animator) | An A2A-only LLM agent that renders pixel-art sprites through pixel-art-mcp and attaches the results in Discord. |
| [`bootcamp`](bootcamp) | Lets the bot owner create their own LLM agents, each with its own system prompt and permission group. |
| [`suggestionbox`](suggestionbox) | An MCP server that lets agents post error reports and suggestions to a Discord channel. |
| [`telephonepole`](telephonepole) | Registers third-party MCP servers and chooses which agents may use their tools. |
| [`toolbox`](toolbox) | Installs Node.js/npm on the bot host, and chooses which commands pico may call as LLM tools. |
| [`deskutils`](deskutils) | Small utilities: current time, character/word counts, message quotes. |
| [`switchboard`](switchboard) | Installs requirements for `[p]addpath` cogs and loads/unloads cogs in dependency order. |
| [`testbench`](testbench) | Developer tool, hidden from `[p]cog list`: publishes hand-built agent events to test cctv. |

## Installation

Requires Red 3.5.24 or later on Python 3.11 or later.

```text
[p]load downloader
[p]repo add pixel-agents-cogs https://github.com/pixel-agents-hq/pixel-agents-cogs
[p]cog install pixel-agents-cogs corridor <cog>
[p]load <cog>
```

Every cog except `switchboard` needs `corridor`, and `cctv`, `floorplan`,
`architect` and `painter` also need `pixelagents`. Downloader does not
install these for you, so name them in `[p]cog install` (for example
`[p]cog install pixel-agents-cogs corridor pixelagents cctv`). Once
installed, each cog loads its dependencies itself, so `[p]load <cog>` is
enough. Hidden cogs (`corridor`, `testbench`) do not appear in
`[p]cog list` but install by name. Each cog's install message lists its
outside services and setup; the sections below cover the same ground in
more detail.

Running the cogs from a `[p]addpath` folder instead? Red installs no
requirements for those. Load `switchboard` first and use
`[p]switchboard load <cogs>` to install requirements and load cogs in
dependency order.

## Setup

### LLM connection and A2A listener (corridor)

`pico`, `architect`, `painter`, `animator` and every `bootcamp` agent share
one LLM connection, set by the bot owner:

```text
[p]corridor llm endpoint <url>   # LiteLLM proxy; default https://litellm.nntin.xyz/
[p]corridor llm key <key>        # required; the message is deleted immediately
[p]corridor llm model <model>    # default chatgpt/gpt-5.5
```

No LLM request is made until a key is set. The A2A agents mount on
corridor's HTTP listener, which binds `127.0.0.1:8931` by default
(`[p]corridor a2a host/port`) and answers only the bot's own agent cogs,
which hold a token minted on each load. Permission groups and reply style are set per
server in `[p]corridorsettings`.

### Webview build (pixelagents)

On load, `pixelagents` clones the pinned Pixel Agents webview and the pixatar
CLI (from [pixel-agents-hq/index](https://github.com/pixel-agents-hq/index))
from GitHub, then runs `npm ci` and a Vite build. The host needs `git` and
Node.js 22+ with `npm`; if Node is missing, `[p]toolbox node install`
downloads it from nodejs.org. Checkouts, `node_modules`, build output and a
cache of character avatar images live in the cog's data folder, so expect
disk, network and CPU use on first load. A failed build does not stop the
cog loading; `[p]pixelagents webview rebuild` retries it.

### Office pages (cctv)

- Install and set up AAA3A's [Red Web Dashboard](https://github.com/AAA3A-AAA3A/Red-Web-Dashboard)
  ([installation docs](https://red-web-dashboard.readthedocs.io/en/latest/)):
  every browser page under `/third-party/cctv` is a Dashboard third-party
  page, so without it nothing renders in a browser. cctv loads and its
  commands work without it.
- cctv's aiohttp listener binds `127.0.0.1:3210` by default
  (`[p]cctv host/port`). Route `/cctv/office/<server id>/ws` and
  `/cctv/editor/ws` to it through a reverse proxy, and set
  `[p]cctv publicurl https://<dashboard host>` so `[p]cctv url` gives
  absolute links.
- `[p]cctv enable` opens a server's office. Offices are public by default;
  `[p]cctv visibility unlisted|private` restricts them. An office shows
  message text only from channels `@everyone` can read.
- Anyone who can reach the editor page (the agents' shared office) can
  watch it; only the bot owner, signed in to the Dashboard, can edit it
  (`[p]cctv editors set` opts in a corridor permission group).
- Showing offline members and presences needs the members and presences
  gateway intents.

### Layout catalogue (floorplan)

`floorplan` talks to the [Pixel Index](https://github.com/pixel-agents-hq/index)
API (default `https://pixel-index-api-staging.nntin.xyz`) and links to its
website (default `https://pixel-index.vercel.app`). Change them with
`[p]floorplan index set <url>` and `[p]floorplan index setweb <url>`.

### Agents

- **pico**: off by default; `[p]pico enabled true` turns it on in a server.
  It reads messages there, so the bot needs the message content intent.
- **architect, painter**: no setup beyond the LLM connection; pico reaches
  them as `consult_architect`/`consult_painter`.
- **animator**: run a [pixel-art-mcp](https://github.com/NNTin/pixel-art-mcp)
  server, load `telephonepole`, then
  `[p]telephonepole add pixel-art <url>` and
  `[p]telephonepole agents pixel-art` to grant animator access.
- **bootcamp**: `[p]bootcamp create` (bot owner) opens a panel to create an
  agent; `[p]bootcamp ask <agent_key> <prompt>` consults it directly.

### MCP tools (suggestionbox, telephonepole)

`suggestionbox` runs an MCP server on `127.0.0.1:8934` by default
(`[p]suggestionbox mcp host/port`) and, like corridor's A2A listener,
answers only the bot's own cogs; set its channel with
`[p]suggestionbox channel #channel` and grant agents with
`[p]suggestionbox agents`. `telephonepole` connects to the third-party MCP
servers you add with `[p]telephonepole add <name> <url>`; grant agents with
`[p]telephonepole agents <name>`.

## What leaves the bot

| Destination | Sent by | What |
|---|---|---|
| LLM endpoint (`[p]corridor llm endpoint`) | pico, architect, painter, animator, bootcamp | Message content and recent channel history (pico, in enabled servers), A2A prompts, office layouts, tool results, rendered images (animator) |
| MCP servers you register | telephonepole, animator, bootcamp agents | Tool-call arguments chosen by the agent |
| GitHub, npm registry | pixelagents | Clones and package installs for the webview and pixatar builds |
| nodejs.org | toolbox | Node.js release download, only on `[p]toolbox node install` |
| PyPI | switchboard | `pip install` of cog requirements |
| Pixel Index | floorplan | Catalogue searches and layout downloads |

`corridor`, `cctv` and `suggestionbox` also accept connections on their own
listeners, bound to `127.0.0.1` by default. No reply from any cog pings a
user, role or `@everyone`. See [`docs/security-review.md`](docs/security-review.md)
for what each listener accepts and what the agents can do.

## Development

Coding agents start at [`AGENTS.md`](AGENTS.md). See
[`docs/AGENTS.md`](docs/AGENTS.md) for each package's purpose, the
local quality gate and where the design docs live, and each cog's own
`README.md` for its commands.

## Credits

- [Pixel Agents](https://github.com/pixel-agents-hq/pixel-agents) by Pablo
  De Lucca (MIT): the office webview `pixelagents` builds and `cctv` serves,
  including its bundled FS Pixel Sans font. It is not committed here: it is
  cloned at the pinned commit on the bot host, and every build carries
  upstream's license as `LICENSE.txt`, served beside the office page.
- [Metro City](https://jik-a-4.itch.io/metrocity-free-topdown-character-pack)
  by JIK-A-4: the character art Pixel Agents' wardrobe is based on.
- [Pixel Index](https://github.com/pixel-agents-hq/index) (MIT): the layout
  catalogue `floorplan` browses and the pixatar character CLI.
- [pixel-art-mcp](https://github.com/NNTin/pixel-art-mcp): the renderer
  `animator` drives.
- [Red Web Dashboard](https://github.com/AAA3A-AAA3A/Red-Web-Dashboard) by
  AAA3A ([docs](https://red-web-dashboard.readthedocs.io/en/latest/)): the
  web dashboard every `cctv` page runs on. It serves the office webview,
  signs users in with Discord and checks server membership for private
  offices; the browser views depend on it.
- [Red-DiscordBot](https://github.com/Cog-Creators/Red-DiscordBot) by Cog
  Creators.

## License

[MIT](LICENSE).
