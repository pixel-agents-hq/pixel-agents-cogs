# Reply identity: per-cog author names/avatars and consulted-agent footer identity

## Overview

`corridor.send_reply`/`render_reply` is the one path every dependent cog
uses to post a Discord reply, respecting a guild's configured `ReplyMode`
(`EMBED` or `TEXT`). Every dependent cog binds its own `ReplyIdentity` once
— a name plus an avatar — via `CogBase.reply_sender()`,
and every reply that cog sends through the resulting `ReplySender` carries
that identity as the embed's author line (or a text prefix in `TEXT`
mode), with zero per-call-site repetition across the 60+ places
`send_reply`/`render_reply` are called.

A second, narrower identity concept — `FooterOverride` — lets one message
show a *different* party's identity in its footer, distinct from the
sending cog's own author line and from the guild's configured footer.
`pico/tools/consult_agent_tool.py`'s `ConsultAgentTool` is the one real
consumer: when pico relays an A2A exchange, the footer shows the
*consulted* agent's name and avatar, not pico's own.

An agent's avatar is its office character. A cog that replies as an agent
(pico, architect, painter, animator, every bootcamp agent) also passes
`reply_sender(agent=...)`: each send asks `CogBase.avatar_for(agent)` for
that agent's avatar, which pixelagents answers with its pixatar character
(`pixelagents.office_character_avatar_path`), the same look it wears on
the CCTV pages. An agent has no bundled image: while it has no character
avatar (pixelagents not loaded, pixatar unavailable) its replies carry no
icon. Only the non-agent cogs bind a bundled `avatar_path`.

## Architecture

`ReplyIdentity`, `FooterOverride`, and `ReplyCategory` are plain,
framework-neutral dataclasses/enums in `corridor/domain/models.py` — zero
discord.py imports, matching every other domain type in that module. Each
dependent cog obtains one bound `ReplySender` (`corridor/adapters/reply_sender.py`)
from `CogBase.reply_sender()` at its own `cog_load`, alongside its existing
`register_dependent`/`register_agent` calls, and stores it — every one of
that cog's own `send_reply`/`render_reply` call sites then calls the bound
sender instead of the raw `corridor` reference, with no other argument
changes.

```mermaid
flowchart TB
    Arch["architect<br/><small>reply_sender(owner='Architect',<br/>agent=AgentRef(agent_key='architect'))</small>"]
    Pico["pico<br/><small>reply_sender(owner='Pico', ...)<br/>+ ConsultAgentTool footer override</small>"]
    Painter["painter<br/><small>reply_sender(owner='Painter',<br/>agent=AgentRef(agent_key='painter'))</small>"]
    Other["corridor itself / deskutils / toolbox /<br/>floorplan / pixelagents / testbench / ...<br/><small>reply_sender(owner=..., avatar_path=...)</small>"]

    subgraph C["corridor"]
        RS["ReplySender<br/>(adapters/reply_sender.py)"]
        RSvc["ReplyService.render<br/>(application/reply_service.py)"]
        API["build_reply_payload /<br/>send_rendered_reply<br/>(adapters/api.py)"]
        Dir["AgentDirectoryService"]
        Av["avatar_for(agent)<br/><small>registered resolver</small>"]
    end

    Arch -- "reply_sender() once at cog_load" --> RS
    Pico -- "reply_sender() once at cog_load" --> RS
    Painter -- "reply_sender() once at cog_load" --> RS
    Other -- "reply_sender() once at cog_load" --> RS
    RS --> RSvc --> API
    RS -- "per send, for an agent" --> Av
    PA["pixelagents<br/><small>office character avatar</small>"] -- "register_avatar_resolver()" --> Av
    Arch -- "register_agent(RegisteredAgent(...))" --> Dir
    Pico -- "list_agents(), then avatar_for(each agent)<br/>for the consult footer" --> Dir
```

`ReplySender` lives in the adapters layer, not application, alongside
`CogBase` itself: both eventually hand a real `discord.File`/attachment to
`send_rendered_reply` and resolve `Path.exists()` against the actual
filesystem, framework/infrastructure concerns
`corridor/application/reply_service.py` (pure, zero discord.py imports)
stays free of.

A non-agent cog's own bundled avatar ships as `<cog_package>/assets/avatar.png`,
committed to git, optional, attached fresh via `discord.File` on every
message that needs it and referenced as `attachment://<filename>` — never
served over HTTP, never cached across sends. A cog passes its
*conventional* path (`Path(__file__).resolve().parent.parent / "assets" /
"avatar.png"`) to `reply_sender()` regardless of whether the file
currently exists — existence is checked fresh on every send
(`build_reply_payload`), so dropping a real PNG at that exact path is the
entire change needed to light up that cog's author icon, no code change
required. Today, real `avatar.png` files exist for corridor, deskutils,
floorplan, pixelagents, testbench, and toolbox; suggestionbox and
telephonepole pass the same conventional path with no file there, so their
replies show the author name with no icon. Agents (pico, architect,
painter, animator, bootcamp's agents) bind no bundled path at all --
`reply_sender` refuses `agent` together with `avatar_path`.

### Agents' character avatars

Corridor can't import pixelagents (pixelagents depends on corridor), so it
offers a hook instead: pixelagents registers one async resolver at
`cog_load` (`register_avatar_resolver(resolver, owner="pixelagents")`,
unregistered at `cog_unload`), and `avatar_for(agent: AgentRef)` asks it.
The resolver maps a genuine agent's `agent_key` to its character, and a
Discord account (pico: the bot's own account) to that account's character;
it renders the character's pixatar avatar once per seed code into
pixelagents' data directory and returns that file. `avatar_for` is
best-effort -- a failing resolver is logged and means "no avatar", never a
failed reply.

| Agent | `reply_sender(agent=...)` | Character |
|---|---|---|
| architect / painter / animator | `AgentRef(agent_key="architect")` ... | `agent:architect` ... |
| a bootcamp agent | `AgentRef(agent_key=<its key>)`, per `[p]bootcamp ask` reply, author = the agent's key | `agent:<key>` |
| pico | a callable returning the bot account's `AgentRef` (known once logged in) | `user:<bot id>` |

ConsultAgentTool's footer icon is `avatar_for` the consulted agent (none
while it has no character avatar), and corridor's A2A listener serves the
same file at `/<agent_key>/avatar.png`, the agent card's `icon_url`. The bot owner changes an agent's
character with `[p]cctv character agentset <agent> <px.…>` (see
[`docs/cctv-design.md`](cctv-design.md)).

## Domain model / schema

```python
# corridor/domain/models.py

@dataclass(frozen=True, slots=True)
class ReplyIdentity:
    """Which cog is sending -- bound once per cog via CogBase.reply_sender,
    not repeated at each send_reply/render_reply call site.

    `avatar_filename` is a bare filename (e.g. "avatar.png"), not a path --
    this module has zero framework/filesystem-aware imports. The adapter
    layer (ReplySender) resolves it against the cog's actual bundled asset
    path and checks the file exists fresh on every send; None here means
    "no avatar filename was ever configured," not "the file happens to be
    missing right now.\""""

    owner: str
    avatar_filename: str | None = None


@dataclass(frozen=True, slots=True)
class FooterOverride:
    """Overrides a guild's own configured footer for one message --
    ConsultAgentTool's only consumer today: the *consulted* agent's
    name+avatar, distinct from the calling cog's own author identity and
    from the guild's footer_text/icon preference.

    `icon_filename` is a bare filename, like ReplyIdentity.avatar_filename
    -- not a URL. The consulted agent's avatar is always on the same
    filesystem as the consulting cog (every agent in this repo runs in the
    same bot process), so the adapter layer attaches it as a Discord
    attachment the same reliable way it already does the calling cog's own
    avatar."""

    name: str
    icon_filename: str | None = None


@dataclass(frozen=True, slots=True)
class RenderedReply:
    mode: ReplyMode
    content: str | None
    embed_title: str | None
    embed_description: str | None
    fields: tuple[ReplyField, ...]
    footer_text: str | None
    footer_icon_url: str | None          # guild-configured footer icon (a real URL)
    show_timestamp: bool
    author_name: str | None              # ReplyIdentity.owner, or None if unbound
    author_icon_attachment: str | None   # bare filename, set only when
                                          # identity.avatar_filename was given
    category: ReplyCategory | None = None
    footer_icon_attachment: str | None = None
    # Mutually exclusive with footer_icon_url -- set instead of it when the
    # footer comes from a FooterOverride with an icon_filename.
```

`author_name`/`author_icon_attachment` and `footer_text`/`footer_icon_url`
(or `footer_icon_attachment`) are four fields that each answer one of two
questions — "who is this reply from" and "whose identity does the footer
show" — mirroring how `ReplyField`'s own four fields (`name`/`value`/
`inline`/`code`) all answer "what does one embed field look like."

`ReplyCategory` (`AGENT`/`ROOM`/`FURNITURE`) selects an embed's accent
color via `REPLY_CATEGORY_COLORS` (`corridor/domain/reply_colors.py`);
`None` — the default, and what deskutils and pixelagents deliberately keep
— means "no opinion," Discord's own gray, not a fourth category.

**`corridor/domain/agent_directory.py`'s `RegisteredAgent`** carries no
avatar: a registered agent's avatar is `avatar_for` its `agent_key`, which
reaches both this doc's consulted-agent footer path and the A2A protocol's
own `icon_url` (`/<agent_key>/avatar.png` on corridor's shared listener).

`card_with_url(card, url, *, icon_url=None)` rewrites the registering
agent's card at the same call site corridor already uses to rewrite
`supported_interfaces[0].url` to its own host/port — the registering agent
has no way to know either URL ahead of time, since every agent's A2A
surface is mounted on corridor's one shared listener rather than a
listener of its own.

## Key flows

A plain command reply through a cog's own bound sender:

```mermaid
sequenceDiagram
    participant Cmd as architect: status command
    participant RS as self._reply (ReplySender)
    participant RSvc as ReplyService.render
    participant API as send_rendered_reply

    Cmd->>RS: send_reply(ctx, title="Architect Status", fields=fields)
    RS->>RS: avatar_for(AgentRef(agent_key="architect"))<br/>-> pixelagents' HASH.png (or None)
    RS->>RSvc: render(..., identity=ReplyIdentity("Architect", "HASH.png"), category=AGENT)
    RSvc-->>RS: RenderedReply(author_name="Architect",<br/>author_icon_attachment="HASH.png", ...)
    RS->>API: send_rendered_reply(ctx, rendered, avatar_path=pixelagents-data/avatars/HASH.png)
    API->>API: build_reply_payload -- attach discord.File,<br/>set_author(name="Architect", icon_url="attachment://HASH.png")
    API-->>Cmd: discord.Message sent, embed author = Architect
```

`ConsultAgentTool` announcing an A2A exchange, with pico's own author
identity and the consulted agent's footer identity both visible on one
message:

```mermaid
sequenceDiagram
    participant Tool as ConsultAgentTool.handler
    participant RS as pico's self._reply (ReplySender)
    participant RSvc as ReplyService.render
    participant API as send_rendered_reply

    Note over Tool: built with footer_icon_path=<br/>avatar_for(architect)
    Tool->>Tool: FooterOverride(name="architect",<br/>icon_filename="HASH.png")
    Tool->>RS: send_reply(ctx, description="Asking architect: ...",<br/>footer_override=override, footer_icon_path=path)
    RS->>RSvc: render(..., identity=ReplyIdentity("Pico", ...), footer_override=override)
    RSvc-->>RS: RenderedReply(author_name="Pico", ...,<br/>footer_text="architect", footer_icon_attachment="HASH.png")
    RS->>API: send_rendered_reply(ctx, rendered,<br/>avatar_path=pico's character, footer_icon_path=architect's character)
    API->>API: two discord.File attachments (author + footer,<br/>footer prefixed "footer_" to avoid a filename collision)
    API-->>Tool: message shows Pico as author, architect in the footer
```

## API reference

**`corridor.adapters.cog_base.CogBase`**:

```python
def reply_sender(
    self, *, owner: str, avatar_path: Path | None = None, category: ReplyCategory | None = None,
    agent: AgentRef | Callable[[], AgentRef | None] | None = None,
) -> ReplySender: ...

def register_avatar_resolver(
    self, resolver: Callable[[AgentRef], Awaitable[Path | None]], *, owner: str
) -> None: ...
def unregister_avatar_resolver(self, owner: str) -> None: ...
async def avatar_for(self, agent: AgentRef) -> Path | None: ...

async def render_reply(
    self, ctx, *, title=None, description=None, content=None, fields=(), code=(),
    identity: ReplyIdentity | None = None, footer_override: FooterOverride | None = None,
    category: ReplyCategory | None = None,
) -> RenderedReply: ...

async def send_reply(
    self, ctx, *, title=None, description=None, content=None, fields=(), code=(),
    identity: ReplyIdentity | None = None, footer_override: FooterOverride | None = None,
    category: ReplyCategory | None = None,
) -> discord.Message: ...
```

**`corridor.adapters.reply_sender.ReplySender`** — the bound, per-cog
object every call site actually uses; `identity`/`category` are captured
at construction and threaded through every call automatically:

```python
class ReplySender:
    def __init__(
        self, cog_base: CogBase, *, owner: str,
        avatar_path: Path | None = None, category: ReplyCategory | None = None,
        agent: AgentRef | Callable[[], AgentRef | None] | None = None,
    ) -> None: ...

    async def render_reply(self, ctx, *, title=None, description=None,
                            content=None, fields=(), code=()) -> RenderedReply: ...

    async def send_reply(
        self, ctx, *, title=None, description=None, content=None, fields=(), code=(),
        footer_override: FooterOverride | None = None, footer_icon_path: Path | None = None,
    ) -> discord.Message: ...

    async def render_channel_reply(self, guild_id: int, *, ...) -> RenderedReply: ...
    async def send_channel_reply(self, channel, guild_id: int, *, ...) -> discord.Message: ...
    async def publish_event(self, event: object) -> None: ...
```

`render_channel_reply`/`send_channel_reply` are `render_reply`/`send_reply`'s
twin for a caller with no live `ctx` (e.g. a proactive notification into a
configured channel). `publish_event` is forwarded, not duplicated —
`ReplyTool` needs both this and `send_reply` from one object it's handed.

**`corridor.adapters.api`**:

```python
def build_reply_payload(
    reply: RenderedReply, *, avatar_path: Path | None = None, footer_icon_path: Path | None = None,
) -> tuple[dict[str, Any], list[discord.File]]: ...

async def send_rendered_reply(
    ctx: commands.Context, reply: RenderedReply, *,
    avatar_path: Path | None = None, footer_icon_path: Path | None = None,
) -> discord.Message: ...

async def send_rendered_reply_to_channel(
    channel: discord.abc.Messageable, reply: RenderedReply, *,
    avatar_path: Path | None = None, footer_icon_path: Path | None = None,
) -> discord.Message: ...
```

`build_reply_payload` is the single place that turns a `RenderedReply`
into embed/content kwargs plus attachments — shared by
`send_rendered_reply`/`send_rendered_reply_to_channel` and by floorplan's
and pixelagents' own interaction-aware `ReplyMixin`s (which need
ephemeral-response/hybrid-command-followup dispatch `ctx.send` doesn't
support, but the identical embed-building logic).

## Design rationale

**Identity and category are independent parameters, not fused into one
object.** `ReplyIdentity` answers "which cog sent this" (name + avatar);
`ReplyCategory` answers "which visual accent bucket does this belong to"
(agent/room/furniture, or none). A cog's identity never changes across its
own calls, but its category can — architect might send one reply
categorized `AGENT` and, in principle, another with no category at all.
Fusing them would force every identity to carry exactly one fixed color,
when the two questions are genuinely independent: `reply_sender()` accepts
both as separate keyword arguments precisely so a cog's bound sender can
default a category (most do, once, at construction) without that becoming
part of what "being this cog" means.

**Footer icons are Discord attachments from a local file, not the
A2A card's `icon_url`.** Corridor's shared A2A listener does serve a real
`/<agent_key>/avatar.png` route, and does set `AgentCard.icon_url` to that
address — genuine, correct A2A protocol infrastructure for an external A2A
client that doesn't share this process's filesystem. But that address is
only ever reachable from wherever corridor's `a2a_host`/`a2a_port`
actually bind — `127.0.0.1` by default — which works fine for this same
process's own agent-to-agent calls but means Discord's own servers can
never fetch it: a footer built from `icon_url` shows the consulted agent's
*name* correctly but never its icon. Since every agent in this repo shares
pico's own filesystem and process, `ConsultAgentTool` instead takes the
file `avatar_for` returns directly and attaches it as a Discord
attachment — `FooterOverride.icon_filename` plus a `footer_icon_path`
threaded through `ReplySender.send_reply`/`build_reply_payload`, the exact
same reliable attachment mechanism already used for a cog's own author
icon. The footer attachment is filename-prefixed (`footer_<name>`) because
Discord requires unique filenames among one message's attachments, and two
agents dressed in the same seed code share one avatar file name — without
the prefix, a message carrying both the sending agent's author icon and a
consulted agent's footer icon could collide.

**No new cross-cog registry for reply identity.** Unlike
`AgentDirectoryService`/`ToolRegistryService`, which other cogs genuinely
query at runtime, nothing but the registering cog itself ever needs to
look up its own reply identity later — `ReplySender` is held directly by
the cog that created it, never looked up by name elsewhere.

**Call-site ergonomics: a bound sender obtained once, not a repeated
`owner` kwarg.** Every dependent cog's `cog_load` calls
`self._corridor.reply_sender(owner=..., avatar_path=...)` (or
`agent=...`) once and stores
the result as `self._reply`; every existing `send_reply`/`render_reply`
call site keeps its exact signature (title/description/content/fields/code)
— only *which object* receives the call changes, from `self._corridor` to
`self._reply`. Repeating an explicit `owner: str` kwarg at every call site
was considered and rejected: it means touching every call site's
*arguments*, not just which object they call, and risks a future call
site simply forgetting to pass it.

## Non-goals

- **TEXT-mode footer identity.** `ReplyService.render`'s `TEXT` branch
  drops `footer_override` entirely — a footer is strictly an embed
  concept. A guild running `TEXT` mode sees the sending cog's own
  `"**Pico:** ..."` prefix on `ConsultAgentTool`'s announcements, and the
  announcement text itself already names the consulted agent in prose
  ("Asking **architect**: ..."), so only the *icon* is unavailable there,
  never the attribution.
- **Avatar CDN-fronting.** An agent's avatar is rendered once per seed
  code and kept on disk, but the `discord.File` re-upload path and
  corridor's `/avatar.png` A2A route still read that file on every
  call/request — a small avatar re-uploaded on every reply that needs it
  is an accepted, deliberate cost.
- **Avatar auth/access control on corridor's A2A listener.** The avatar
  route shares the same trust model as every other route corridor's
  shared A2A listener serves — no auth. Serving a static PNG adds no
  attack surface beyond "an unauthenticated network peer can read this
  small public-facing image," categorically less sensitive than the
  JSON-RPC surface already accepted there.
- **An avatar upload command.** A non-agent cog's avatar is a
  git-committed asset; an agent's is its office character, changed with
  `[p]cctv character agentset <agent> <px.…>`, never an uploaded image.
- **A guild-configurable "hide cog author lines" toggle.** The author name
  always shows once an identity is bound; there is no `ReplyPreferences`
  field to suppress it.
