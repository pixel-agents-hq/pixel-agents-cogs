# Reply identity: per-cog author names/avatars and consulted-agent footer identity

**Status: proposed.** This document describes what's being designed, not
what's running today — no code from this document has been implemented
yet (unlike `docs/agent-directory-design.md`, which reached this same
"design-only" state before a follow-up implementation pass).

## 1. Problem

`corridor.send_reply`/`render_reply` (`corridor/adapters/cog_base.py`) is
the one path every dependent cog uses to post a Discord reply, respecting
a guild's configured `ReplyMode`. Today's `RenderedReply`
(`corridor/domain/models.py`) carries exactly one `icon_url`, resolved
once per guild from `ReplyPreferences.icon` (BOT/SERVER/CUSTOM), and
`send_rendered_reply` (`corridor/adapters/api.py`) reuses that single
value for *both* the embed author icon
(`embed.set_author(name=reply.embed_title or "", icon_url=reply.icon_url)`,
gated behind `if reply.icon_url:`) and the embed footer icon. There is no
concept anywhere in this pipeline of "which cog sent this" — the author
line today is really the reply's *title*, not an identity, and disappears
entirely whenever a call omits a title or the guild has no icon
configured.

Three requirements motivate a change:

1. Each cog may optionally ship a bundled avatar image. If one exists, the
   embed author *icon* uses it. Regardless of whether one exists, the
   embed author *name* should always show the sending cog's name — a real
   behavior change from today's icon-gated, title-sourced author line.
2. `send_reply` is called from 60+ sites across ~11 files
   (`architect/adapters/commands.py`, `architect/adapters/office_commands.py`,
   `pico/adapters/commands.py`, `pico/tools/reply_tool.py`,
   `pico/tools/consult_agent_tool.py`, `deskutils/adapters/commands.py`,
   `toolbox/adapters/commands.py`, `corridor/adapters/commands.py`,
   `floorplan/adapters/replies.py`, `pixelagents/adapters/replies.py`,
   `testbench/adapters/commands.py`). Whatever mechanism carries "which
   cog is this" must be set up once per cog, not repeated as a kwarg at
   every call site.
3. `pico/tools/consult_agent_tool.py`'s `ConsultAgentTool` posts two
   announcements per A2A consult call (the outgoing question, then the
   target agent's raw answer/failure — see
   `docs/agent-directory-design.md` §5's addendum). Its embed *footer*
   should show the **consulted agent's** (e.g. architect's) identity —
   distinct from pico's own author identity on that same message, and
   distinct from the guild's own configured footer.

Requirement 3 is a genuinely different delivery problem from 1: a
consulted agent's avatar has to cross the A2A network boundary from
architect's process/package to pico's rendered embed. A footer icon that
isn't `attachment://`-based on the *current* message must be a real
HTTP(S) URL — pico cannot forward raw bytes for an avatar representing a
different party than the message's own attachments.

## 2. Locked decisions

Decided explicitly for this design, mirroring `agent-directory-design.md`'s
"Locked decisions" convention:

- **A cog's own author icon ships as a bundled file** —
  `<cog_package>/assets/avatar.png`, committed to git, optional —
  attached fresh via `discord.File` on every message that needs it,
  referenced as `attachment://<filename>`. No Red Dashboard dependency,
  no external hosting — this repo has no committed image assets and no
  `discord.File`/`attachment://` usage today, and the one existing
  "cog serves a static asset via URL" pattern (`WebviewAssetProvider`,
  built for floorplan/architect's much larger vendored `webview_dist/`
  build output) requires Red's Web Dashboard loaded with no fallback —
  reusing it for a tiny optional icon was considered and rejected as the
  wrong tool for this size of asset. Accepted tradeoff: the (small) file
  is re-uploaded on every reply that uses it — deliberate simplicity over
  premature caching.
- **Call-site ergonomics: a bound per-cog reply sender, obtained once.**
  Each dependent cog's own `CogBase.__init__`/`cog_load` calls
  `self._corridor.reply_sender(owner=..., avatar_path=...)` once —
  alongside the existing `register_dependent`/`register_agent` calls —
  and stores the result. Every one of the 60+ existing `send_reply`/
  `render_reply` call sites keeps its exact signature (title/description/
  content/fields/code); only *which object* receives the call changes,
  from `self._corridor` to the bound sender. Rejected alternative: an
  explicit `owner: str` kwarg repeated at every call site (matches
  `register_tool`/`register_agent`'s literal convention more closely, but
  means touching 60+ call sites' *arguments*, not just which object they
  call, and risks a future call site simply forgetting to pass it).
- **No new cross-cog registry** for reply identity. Unlike
  `AgentDirectoryService`/`ToolRegistryService` — which other cogs
  genuinely query at runtime (pico calls `list_agents()`/
  `list_tools_for()` every turn) — nothing but the registering cog itself
  ever needs to look up its own reply identity later. A stored registry
  here would have zero consumers other than the registrant, permanently;
  building one anyway is exactly the premature complexity this design
  avoids.

```mermaid
flowchart TB
    Arch["architect<br/><small>reply_sender(owner=\"Architect\",<br/>avatar_path=.../assets/avatar.png)</small>"]
    Pico["pico<br/><small>reply_sender(owner=\"Pico\", ...)<br/>+ ConsultAgentTool footer override</small>"]
    Other["deskutils / toolbox / floorplan / ...<br/><small>reply_sender(owner=..., ...)</small>"]
    C["corridor<br/><small>CogBase.render_reply/send_reply<br/>(identity + footer_override params)<br/>+ shared A2A listener serving<br/>/&lt;agent_key&gt;/avatar.png</small>"]

    Arch -- "reply_sender() once at cog_load" --> C
    Pico -- "reply_sender() once at cog_load" --> C
    Other -- "reply_sender() once at cog_load" --> C
    Arch -- "register_agent(RegisteredAgent(avatar_path=...))" --> C
    Pico -- "A2A: reads agent.card.icon_url" --> C
    C -. "serves architect's avatar bytes<br/>at /architect/avatar.png" .-> Pico
```

## 3. Domain: `ReplyIdentity`, `FooterOverride`, and `RenderedReply`'s growth

Both new value types are plain, framework-neutral dataclasses with zero
discord.py/a2a-sdk imports, so they belong directly in
`corridor/domain/models.py` — **no new domain module is needed** here.
Unlike `agent_directory.py` (split out specifically because it stores the
real protobuf `AgentCard`, a deliberate framework-type exception), nothing
about reply identity needs a framework type: an owner name is a `str`, an
avatar reference is a bare filename `str` (not a `Path` — `Path`
resolution/existence-checking is adapter-layer work, done by `ReplySender`
and `send_rendered_reply`, never something the domain layer touches).

```python
# corridor/domain/models.py -- new types, alongside ReplyField/RenderedReply

@dataclass(frozen=True, slots=True)
class ReplyIdentity:
    """Which cog is sending -- bound once per cog via CogBase.reply_sender,
    not repeated at each of the 60+ send_reply/render_reply call sites.

    `avatar_filename` is a bare filename (e.g. "avatar.png"), not a path --
    this module has zero framework/filesystem-aware imports by design. The
    adapter layer resolves it against the cog's actual `assets/` directory
    and checks the file exists at send time (see ReplySender); `None` here
    just means "no avatar filename was ever configured for this identity."
    """

    owner: str
    avatar_filename: str | None = None


@dataclass(frozen=True, slots=True)
class FooterOverride:
    """Overrides a guild's own configured footer for one message --
    ConsultAgentTool's only consumer today: the *consulted* agent's
    name+avatar URL, distinct from the calling cog's own author identity
    and from the guild's footer_text/icon preference. `icon_url` is a real
    HTTP(S) URL (not attachment://) -- see corridor's shared A2A listener
    serving /<agent_key>/avatar.png, §6."""

    name: str
    icon_url: str
```

`RenderedReply` gains two new fields and renames its old single `icon_url`
for clarity now that "icon" splits into two independent concepts (author
vs. footer):

```python
@dataclass(frozen=True, slots=True)
class RenderedReply:
    mode: ReplyMode
    content: str | None
    embed_title: str | None
    embed_description: str | None
    fields: tuple[ReplyField, ...]
    footer_text: str | None
    footer_icon_url: str | None         # renamed from `icon_url` -- guild-configured
                                         # (or FooterOverride-supplied) footer icon only
    show_timestamp: bool
    author_name: str | None             # NEW -- ReplyIdentity.owner, or None for a
                                         # caller that never bound an identity (corridor's
                                         # own internal send_reply/render_reply calls stay
                                         # anonymous, same as today)
    author_icon_attachment: str | None  # NEW -- bare filename, e.g. "avatar.png";
                                         # set only when identity.avatar_filename was given
```

This stays four cohesive fields describing "who this reply is from and
how to show it" — not a kitchen-sink grab-bag, since every field answers
exactly that one question (two for the author line, two for the footer),
mirroring how `ReplyField`'s own four fields all answer "what does one
embed field look like."

**A pre-existing duplication this rename also surfaces and fixes:**
`floorplan/adapters/replies.py` and `pixelagents/adapters/replies.py`'s
own `ReplyMixin` already hand-build a `discord.Embed` from a
`RenderedReply` independently of `send_rendered_reply` (they need
interaction-aware dispatch — ephemeral responses, hybrid-command
followups — `send_rendered_reply`'s plain `ctx.send` doesn't support).
Both re-implement the exact same `set_author`/`set_footer` logic, and both
already source the author name from `embed_title` — the same bug this
design fixes in `send_rendered_reply`, now confirmed to exist in two
extra places that would otherwise silently keep the old, wrong behavior.
This design extracts a shared `build_reply_payload(reply, avatar_path)`
helper in `corridor/adapters/api.py` (embed/content kwargs + attachments,
framework-facing but session-agnostic) that `send_rendered_reply` and both
`ReplyMixin`s all call, instead of three independent implementations of
the same rendering logic.

## 4. `ReplyService.render` (`corridor/application/reply_service.py`)

Gains two new optional parameters: `identity: ReplyIdentity | None = None`
and `footer_override: FooterOverride | None = None`.

- **EMBED mode**: `author_name = identity.owner if identity else None`;
  `author_icon_attachment = identity.avatar_filename if identity else
  None` — always set once an identity is supplied, regardless of whether
  an avatar filename exists (requirement 1's "name always shows," fixing
  today's icon-gated branch). Footer: `footer_text, footer_icon_url =
  (override.name, override.icon_url) if footer_override else
  (preferences.footer_text, await self._resolve_icon(...))` — the
  override wins outright for the one message it's supplied on, taking
  priority over the guild's own configured footer, per requirement 3.
- **TEXT mode**: no embed exists, so there is no icon equivalent at all —
  icons (author avatar, footer icon of either kind) are silently dropped,
  not an error. The owner name still needs to show somehow: prefix the
  rendered `content` with `"**{identity.owner}:** "` when an identity was
  supplied (and the resulting text is non-empty), otherwise leave content
  unchanged. `footer_override`, even if supplied, has no TEXT-mode
  rendering — a footer is strictly an embed concept — so the consulted
  agent's *icon* has no way to reach a TEXT-mode message (its *name* still
  does, via whatever prose the caller already wrote — see §8 on
  `ConsultAgentTool`'s own message text already naming the agent).

## 5. `ReplySender`: where it lives, and what it forwards vs. adds

`ReplySender` lives in the **adapters layer**,
`corridor/adapters/reply_sender.py` — not application, despite `CogBase`
already exposing `render_reply`/`send_reply` as its public surface.
Reasoning: `ReplySender.send_reply` must eventually hand a real
`discord.File`/attachment to `send_rendered_reply`, and must resolve
`Path.exists()` against the actual filesystem — both framework/
infrastructure-facing concerns `corridor/application/reply_service.py`
(pure, zero discord.py imports) deliberately stays free of. `CogBase`
itself already lives in `corridor/adapters/cog_base.py` for the identical
reason (it constructs real `discord.Embed`s via `send_rendered_reply`), so
`ReplySender` sits alongside it, not one layer down.

`ReplySender` is a thin, hand-forwarding wrapper — never a
`__getattr__`-based blanket passthrough, so its surface stays intentional
and typed:

```python
# corridor/adapters/reply_sender.py

class ReplySender:
    """Bound once per cog, via CogBase.reply_sender(owner=..., avatar_path=...)
    at that cog's own cog_load -- forwards to CogBase's existing
    render_reply/send_reply logic rather than duplicating it; adds nothing
    beyond carrying this cog's own ReplyIdentity through every call.

    `avatar_path`, when given, should be the *conventional* path
    (<cog_package>/assets/avatar.png) regardless of whether that file
    currently exists on disk -- existence is checked fresh on every send
    (see send_rendered_reply), so dropping a real PNG at that exact path
    later lights up icons everywhere with zero code change. Passing None
    outright (rather than a not-yet-existing conventional path) is only
    appropriate for a cog that will never want author icons at all."""

    def __init__(self, cog_base: CogBase, *, owner: str, avatar_path: Path | None = None) -> None:
        self._cog_base = cog_base
        self._avatar_path = avatar_path
        self._identity = ReplyIdentity(
            owner=owner,
            avatar_filename=avatar_path.name if avatar_path is not None else None,
        )

    async def render_reply(
        self, ctx: commands.Context, *, title=None, description=None,
        content=None, fields=(), code=(),
    ) -> RenderedReply:
        return await self._cog_base.render_reply(
            ctx, title=title, description=description, content=content,
            fields=fields, code=code, identity=self._identity,
        )

    async def send_reply(
        self, ctx: commands.Context, *, title=None, description=None,
        content=None, fields=(), code=(),
        footer_override: FooterOverride | None = None,
    ) -> discord.Message:
        rendered = await self._cog_base.render_reply(
            ctx, title=title, description=description, content=content,
            fields=fields, code=code, identity=self._identity,
            footer_override=footer_override,
        )
        return await send_rendered_reply(ctx, rendered, avatar_path=self._avatar_path)

    async def publish_event(self, event: object) -> None:
        """Forwarded, not duplicated -- ReplyTool needs both this and
        send_reply from one object it's handed (§8); everything else a
        caller might want from corridor stays reached through the plain
        `corridor` reference passed alongside this one, never guessed at
        via a blanket passthrough here."""

        await self._cog_base.publish_event(event)
```

`CogBase.reply_sender` (`corridor/adapters/cog_base.py`) is the one-line
factory:

```python
def reply_sender(self, *, owner: str, avatar_path: Path | None = None) -> ReplySender:
    return ReplySender(self, owner=owner, avatar_path=avatar_path)
```

`CogBase.render_reply`/`send_reply` gain the two new optional parameters
(`identity: ReplyIdentity | None = None`, `footer_override: FooterOverride
| None = None`, both defaulting to `None`) and simply forward them into
`ReplyService.render`. Corridor's own `CommandsMixin`
(`corridor/adapters/commands.py`) keeps calling `self.send_reply(...)`
bare, with no identity — its own replies stay anonymous, exactly today's
behavior; corridor is the implementer of this whole mechanism, not a
dependent that needs to be told apart from other cogs on its own
commands. (A later, purely additive pass could give corridor its own
`self._reply = self.reply_sender(owner="Corridor")` if ever wanted — out
of scope here, not required.)

## 6. `send_rendered_reply` changes (`corridor/adapters/api.py`)

```python
def build_reply_payload(
    reply: RenderedReply, *, avatar_path: Path | None = None
) -> tuple[dict[str, Any], list[discord.File]]:
    """embed/content kwargs + attachments, shared by send_rendered_reply
    and floorplan's/pixelagents' own interaction-aware ReplyMixin dispatch
    (§3's pre-existing-duplication fix)."""

    if reply.mode is ReplyMode.TEXT:
        return {"content": reply.content}, []  # author-name prefix already
                                                 # applied by ReplyService.render;
                                                 # icons have no TEXT equivalent

    embed = discord.Embed(title=reply.embed_title, description=reply.embed_description)
    for field in reply.fields:
        embed.add_field(name=field.name, value=field.value, inline=field.inline)

    files: list[discord.File] = []
    author_icon_url: str | None = None
    if reply.author_icon_attachment and avatar_path is not None and avatar_path.exists():
        # Re-read from disk on every call -- deliberate simplicity over
        # premature caching (Locked Decisions, §2). A small avatar PNG
        # re-uploaded on every reply that needs it is an accepted cost.
        files.append(discord.File(avatar_path, filename=reply.author_icon_attachment))
        author_icon_url = f"attachment://{reply.author_icon_attachment}"

    if reply.author_name:
        # ALWAYS set once an identity is bound -- regardless of whether an
        # avatar exists, unlike today's `if reply.icon_url:`-gated branch.
        embed.set_author(name=reply.author_name, icon_url=author_icon_url)
    if reply.footer_text:
        embed.set_footer(text=reply.footer_text, icon_url=reply.footer_icon_url)
    if reply.show_timestamp:
        embed.timestamp = discord.utils.utcnow()

    return {"embed": embed}, files


async def send_rendered_reply(
    ctx: commands.Context, reply: RenderedReply, *, avatar_path: Path | None = None
) -> discord.Message:
    kwargs, files = build_reply_payload(reply, avatar_path=avatar_path)
    return await ctx.send(files=files, **kwargs)
```

`ctx.send(files=[], **kwargs)` with an empty attachment list is a normal,
harmless call in discord.py — no need to special-case the zero-file path.

## 7. Consulted-agent footer identity: corridor serves `/<agent_key>/avatar.png`

Corridor's existing shared A2A listener (`docs/agent-directory-design.md`)
is reused as-is — no second HTTP surface. This is the same "corridor
rewrites a URL field on a card it's mounting" shape `card_with_url`
(`corridor/domain/agent_directory.py`) already establishes for
`supported_interfaces[0].url`, just adding a second field (`icon_url`,
confirmed present but unused on the real `a2a.types.AgentCard`) at the
same call site, and reusing the exact same per-agent
`Mount(f"/{agent_key}", ...)` the shared listener already builds.

**`corridor/domain/agent_directory.py`:**

```python
@dataclass(frozen=True, slots=True)
class RegisteredAgent:
    agent_key: str
    card: AgentCard
    executor: AgentExecutor
    avatar_path: Path | None = None  # NEW -- same bare-filename-on-disk
                                      # convention ReplyIdentity/ReplySender
                                      # use for a cog's own author avatar;
                                      # corridor reads it fresh per request,
                                      # never caches its bytes.


def card_with_url(card: AgentCard, url: str, *, icon_url: str | None = None) -> AgentCard:
    """Extended: also sets AgentCard.icon_url when `icon_url` is given --
    the registering agent has no more way to know its own eventual
    host/port for this than it does for supported_interfaces[0].url, so
    corridor sets both here, at the same call site, for the same reason."""

    rewritten = AgentCard()
    rewritten.CopyFrom(card)
    del rewritten.supported_interfaces[:]
    rewritten.supported_interfaces.add(url=url, protocol_binding=TransportProtocol.JSONRPC.value)
    if icon_url is not None:
        rewritten.icon_url = icon_url
    return rewritten
```

**`corridor/infrastructure/a2a_server.py`'s `_build_routes`:** each
agent's existing `Mount(f"/{agent.agent_key}", routes=agent_routes)` gains
one extra `Route` inside `agent_routes` when `agent.avatar_path` is set:

```python
def _avatar_route(avatar_path: Path) -> Route:
    async def handler(request: Request) -> Response:
        if not avatar_path.exists():
            return Response(status_code=404)
        return FileResponse(avatar_path)  # streams fresh from disk per
                                           # request -- same no-caching
                                           # tradeoff as ReplySender's own
                                           # attachment re-upload
    return Route("/avatar.png", handler)

# inside _build_routes, per agent:
if agent.avatar_path is not None:
    agent_routes.append(_avatar_route(agent.avatar_path))
```

**`corridor/adapters/cog_base.py`'s `register_agent`:** builds the avatar
URL the same way it already builds the A2A base URL, and passes it into
the extended `card_with_url`:

```python
async def register_agent(self, agent: RegisteredAgent, *, owner: str) -> None:
    settings = await self._repository.a2a_settings()
    url = f"http://{settings.a2a_host}:{settings.a2a_port}/{agent.agent_key}/"
    icon_url = (
        f"http://{settings.a2a_host}:{settings.a2a_port}/{agent.agent_key}/avatar.png"
        if agent.avatar_path is not None else None
    )
    rewritten = RegisteredAgent(
        agent_key=agent.agent_key,
        card=card_with_url(agent.card, url, icon_url=icon_url),
        executor=agent.executor,
        avatar_path=agent.avatar_path,
    )
    self._agent_directory.register(rewritten, owner=owner)
    self._a2a_server.rebuild_routes(self._agent_directory.list_agents())
```

**`architect/adapters/cog_base.py`'s `_register_with_corridor`:** passes
its own conventional avatar path, same "always pass the conventional path,
existence-checked later" pattern §9 uses everywhere else:

```python
await self._corridor.register_agent(
    RegisteredAgent(
        agent_key="architect",
        card=card,
        executor=self._executor,
        avatar_path=Path(__file__).resolve().parent.parent / "assets" / "avatar.png",
    ),
    owner="architect",
)
```

`architect/infrastructure/a2a_server.py`'s `build_agent_card` itself does
**not** set `icon_url` — matching the existing precedent that corridor,
not the registering agent, owns every URL field on a card it mounts (the
same reasoning `supported_interfaces[0].url`'s placeholder already
follows).

**A2A auth/exposure note:** the avatar route has exactly the same trust
model as every other route corridor's shared A2A listener already serves
— no auth, same explicit non-goal `docs/agent-directory-design.md` §7
already states for the JSON-RPC routes. Serving a static PNG adds no new
attack surface beyond "an unauthenticated network peer can read this
small public-facing image," categorically less sensitive than the
JSON-RPC surface already accepted there.

## 8. `ConsultAgentTool`/`ReplyTool` wiring

**`pico/adapters/listener.py`'s `_agent_tools`:** reads the (possibly
empty-string, protobuf-default) `agent.card.icon_url` and normalizes it to
`None`, passing it through:

```python
def _agent_tools(corridor: Any, reply: ReplySender, client: Any, ctx: commands.Context) -> list[ToolSpec]:
    tools: list[ToolSpec] = []
    for agent in corridor.list_agents():
        try:
            tools.append(
                ConsultAgentTool(
                    client, reply, ctx,
                    agent_key=agent.agent_key,
                    base_url=agent.card.supported_interfaces[0].url,
                    description=agent.card.description,
                    footer_icon_url=agent.card.icon_url or None,
                )
            )
        except Exception:
            log.warning("pico: could not build a tool for agent %r, skipping",
                        agent.agent_key, exc_info=True)
    return tools
```

**`pico/tools/consult_agent_tool.py`:** `ConsultAgentTool.__init__` gains
`footer_icon_url: str | None`, and its `corridor: CorridorReply` param is
replaced by a `reply: ReplySenderProtocol` param whose `send_reply`
accepts `footer_override`. `_announce` builds the override once and
passes it through every call:

```python
class ReplySenderProtocol(Protocol):
    """Structurally satisfied by corridor.adapters.reply_sender.ReplySender."""

    async def send_reply(
        self, ctx: object, *, title=None, description=None, content=None,
        fields: Sequence[ReplyField] = (), footer_override: FooterOverride | None = None,
    ) -> object: ...


class ConsultAgentTool:
    def __init__(
        self, client: ArchitectAsker, reply: ReplySenderProtocol, ctx: object, *,
        agent_key: str, base_url: str, description: str, footer_icon_url: str | None = None,
    ) -> None:
        self.name = f"consult_{agent_key}"
        self.description = description or f"Delegate a task to {agent_key}."
        self._client = client
        self._reply = reply
        self._ctx = ctx
        self._agent_key = agent_key
        self._base_url = base_url
        self._footer_override = (
            FooterOverride(name=agent_key, icon_url=footer_icon_url)
            if footer_icon_url else None
        )

    async def _announce(self, description: str) -> None:
        try:
            await self._reply.send_reply(
                self._ctx, description=description, footer_override=self._footer_override,
            )
        except Exception:
            log.warning("pico: %s could not announce an A2A exchange", self.name, exc_info=True)
```

Both of `ConsultAgentTool`'s own two announcement messages now show
pico's own author identity (name always, icon if pico ever ships one)
from `self._reply`'s bound `ReplyIdentity`, *and* the consulted agent's
name+avatar in the footer via `footer_override` — requirement 3, with
pico's author identity and the target's footer identity visibly distinct
on the same message.

**`pico/tools/reply_tool.py`:** `ReplyTool` needs two capabilities from
what it's handed: `send_reply` (now via `ReplySender`) and `publish_event`
(still a plain corridor method, unrelated to reply identity). Rather than
overload `ReplySender` with an ever-growing forwarded surface, the
constructor takes both, explicitly:

```python
class ReplySenderProtocol(Protocol):
    async def send_reply(self, ctx, *, title=None, description=None, content=None, fields=()) -> SentMessage: ...

class CorridorEvents(Protocol):
    async def publish_event(self, event: object) -> None: ...

class ReplyTool:
    def __init__(
        self, reply: ReplySenderProtocol, corridor: CorridorEvents, ctx: object, *,
        guild_id: int, bot_user_id: int | None,
    ) -> None:
        self._reply = reply
        self._corridor = corridor
        ...

    async def handler(self, raw_input: BaseModel) -> BaseModel:
        ...
        message = await self._reply.send_reply(...)
```

`pico/adapters/listener.py`'s tool assembly passes both, built once per
turn from state set once at `cog_load`:

```python
tools: list[ToolSpec] = [
    ReplyTool(self._reply, self._corridor, ctx, guild_id=guild.id, bot_user_id=_bot_user_id(self.bot)),
]
```

`self._reply` is pico's own bound sender, set once in pico's `cog_load`
exactly like architect's:
`self._corridor.reply_sender(owner="Pico", avatar_path=Path(__file__).resolve().parent.parent / "assets" / "avatar.png")`.

## 9. Representative call-site diff for a plain command cog

**`architect/adapters/commands.py`'s `status` command** (a
`CommandsMixin` site) — object swap only, signature unchanged:

```diff
-        await self._corridor.send_reply(ctx, title="Architect Status", fields=fields)
+        await self._reply.send_reply(ctx, title="Architect Status", fields=fields)
```

`self._reply` is set once in `architect/adapters/cog_base.py`'s
`cog_load` (or `__init__`, mirroring where `self._corridor` itself is
assigned):

```python
self._reply = self._corridor.reply_sender(
    owner="Architect",
    avatar_path=Path(__file__).resolve().parent.parent / "assets" / "avatar.png",
)
```

Every other `self._corridor.send_reply(...)`/`render_reply(...)` call in
`architect/adapters/commands.py` and `office_commands.py` gets the same
one-word object swap; no other argument changes. Every remaining
dependent cog (`deskutils`, `toolbox`, `floorplan`, `pixelagents`,
`testbench`, and any cog scaffolded from the cookiecutter template going
forward) follows this exact pattern: one new `self._reply = ...` line at
`cog_load`, then a mechanical `self._corridor.` → `self._reply.` rename
at each of that cog's own `send_reply`/`render_reply` call sites — this
document doesn't enumerate every one individually, the pattern is
identical everywhere.

## 10. Rollout

Every dependent cog passes its **conventional** avatar path
(`Path(__file__).resolve().parent.parent / "assets" / "avatar.png"`) from
day one, not a literal `None` — existence is checked fresh at send time
(§6), not at `reply_sender()` construction time. This means:

- **First pass, at implementation time:** no cog ships a real PNG at that
  path yet, so `avatar_path.exists()` is `False` everywhere,
  `author_icon_attachment` never actually attaches a file, and every
  embed author line shows *name only* — requirement 1's
  name-always-shows behavior, visible immediately with zero visual
  regression risk (an author line simply appears where none did before).
- **Later, incrementally, per cog:** dropping a real `avatar.png` at that
  exact committed path is the entire change needed to light up that
  cog's icon — no code change, no redeploy logic beyond the file itself.
  Passing the conventional path from day one (rather than a literal
  `None` a future editor would have to remember to swap out) removes that
  future code-edit step entirely.
- A cog that will genuinely never want an author icon can still pass
  `avatar_path=None` explicitly — the option stays available, it's just
  not the recommended default for a dependent cog's first pass.

## 11. Out of scope for this pass

- **TEXT-mode footer identity.** §4 explicitly drops `footer_override` in
  `ReplyMode.TEXT` — the consulted agent's icon has no rendering there in
  this pass. A guild running TEXT mode sees pico's own `"**Pico:** ..."`
  prefix on `ConsultAgentTool`'s announcements, and the announcement text
  itself already names the consulted agent in prose ("Asking
  **architect**: ..."), so only the *icon* is genuinely unavailable, not
  the attribution.
- **Avatar caching/CDN-fronting.** Both the `discord.File` re-upload path
  and corridor's `/avatar.png` route re-read from disk on every single
  call/request — deliberate, already locked (§2). A future pass could add
  in-memory caching keyed by file mtime if re-upload volume ever becomes
  a real cost; not attempted here.
- **Avatar auth/access control on corridor's A2A listener.** Same
  established non-goal as the rest of the A2A surface
  (`docs/agent-directory-design.md` §7) — see §7's note above.
- **A `[p]<cog> avatar` upload/management command.** Avatars are a
  git-committed asset, not admin-configurable at runtime, matching the
  locked "bundled file" decision — no owner-facing command to change one
  without editing the repo.
- **Any cog actually gaining a real avatar image in this pass.** §10's
  rollout wires every dependent cog's `avatar_path` to its conventional
  (currently-nonexistent) location; supplying the actual PNG files is
  separate, cog-by-cog follow-up work, not part of this design's
  implementation checklist.
- **A guild-configurable "hide cog author lines" toggle.** Requirement 1
  is unconditional (name always shows once an identity is bound) — no new
  `ReplyPreferences` field to suppress it is introduced here.
- **Corridor gaining its own bound `self._reply` for its own commands.**
  Corridor's own replies stay anonymous, exactly today's behavior (§5) —
  a purely additive follow-up if ever wanted.

## 12. Implementation checklist

1. `corridor/domain/models.py`: add `ReplyIdentity`, `FooterOverride`;
   rename `RenderedReply.icon_url` → `footer_icon_url`; add
   `author_name`, `author_icon_attachment`.
2. `corridor/application/reply_service.py`: `ReplyService.render` gains
   `identity`/`footer_override` params; EMBED-mode branch sets the four
   new/renamed `RenderedReply` fields per §4; TEXT-mode branch prepends
   the `"**{owner}:** "` prefix and drops every icon/footer concept.
3. `corridor/adapters/api.py`: extract the shared `build_reply_payload`
   helper (§6); `send_rendered_reply` becomes a thin wrapper around it;
   always `set_author` when `author_name` is present; attach
   `discord.File` only when the attachment filename *and* an existing
   `avatar_path` are both present; apply `footer_override` ahead of the
   guild's own configured footer when supplied.
4. New `corridor/adapters/reply_sender.py`: `ReplySender` (§5).
5. `corridor/adapters/cog_base.py`: add `reply_sender()` factory; add
   `identity`/`footer_override` optional params to `render_reply`/
   `send_reply`, forwarded into `ReplyService.render`.
6. `corridor/domain/agent_directory.py`: `RegisteredAgent` gains
   `avatar_path: Path | None = None`; `card_with_url` gains an optional
   `icon_url` param and sets `AgentCard.icon_url`.
7. `corridor/infrastructure/a2a_server.py`: `_build_routes` mounts an
   extra `/avatar.png` `Route` per agent with a set `avatar_path`, serving
   fresh via `FileResponse` (404 if the file doesn't currently exist).
8. `corridor/adapters/cog_base.py`'s `register_agent`: build and pass the
   avatar URL into the extended `card_with_url`, same pattern as the
   existing A2A base-URL construction.
9. `architect/adapters/cog_base.py`: pass `avatar_path=` in its
   `RegisteredAgent(...)` construction; add `self._reply =
   self._corridor.reply_sender(owner="Architect", avatar_path=...)`;
   swap every `self._corridor.send_reply`/`render_reply` call in
   `architect/adapters/commands.py`/`office_commands.py` to
   `self._reply.`.
10. `pico/adapters/cog_base.py`/`listener.py`: add `self._reply =
    self._corridor.reply_sender(owner="Pico", avatar_path=...)`; update
    `_agent_tools` to pass `reply=self._reply` and `footer_icon_url=
    agent.card.icon_url or None` into `ConsultAgentTool`; swap
    `ReplyTool`'s construction to pass both `self._reply` and
    `self._corridor` (§8).
11. `pico/tools/reply_tool.py`: split `CorridorReply` into
    `ReplySenderProtocol` + `CorridorEvents`; constructor takes both
    `reply`/`corridor`.
12. `pico/tools/consult_agent_tool.py`: constructor takes `reply:
    ReplySenderProtocol` instead of `corridor: CorridorReply`, plus
    `footer_icon_url: str | None`; `_announce` builds and passes
    `FooterOverride`.
13. Every remaining dependent cog (`deskutils`, `toolbox`, `floorplan`,
    `pixelagents`, `testbench`, corridor's own cookiecutter template):
    add the one-line `reply_sender()` binding; mechanical
    `self._corridor.` → `self._reply.` swap at each existing call site
    (§9's pattern).
14. `floorplan/adapters/replies.py` and `pixelagents/adapters/replies.py`:
    `ReplyMixin` gains its own bound `ReplyIdentity`, passed through
    `render_reply`; `_render_reply_payload` calls the new
    `build_reply_payload` helper instead of hand-rolling `set_author`/
    `set_footer` itself (§3).
15. Tests: `ReplyService.render` unit tests for identity/footer_override
    in both EMBED and TEXT modes (author-always-shows, icon-gated,
    override-priority-over-guild-footer, TEXT prefix, TEXT drops
    everything else); `build_reply_payload`/`send_rendered_reply`
    attachment-only-when-file-exists behavior; `A2AServer`'s new
    `/avatar.png` route (served when present, 404 when the configured
    file is missing); an end-to-end pico→corridor→architect A2A test
    asserting `ConsultAgentTool`'s two announcements carry pico's author
    identity and architect's footer identity distinctly.
