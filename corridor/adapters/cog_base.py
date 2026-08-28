"""Dependency composition and the public cross-cog API surface.

Other cogs call these methods via `bot.get_cog("Corridor")` -- this is the
stable contract they depend on through `required_cogs`.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path
from typing import Any, TypeVar, cast

import discord
from redbot.core import commands
from redbot.core.bot import Red

from ..application import (
    AgentDirectoryService,
    EventBusService,
    PermissionService,
    ReplyContent,
    ReplyService,
    ToolRegistryService,
)
from ..domain import (
    A2ASettings,
    FooterOverride,
    GuildSettings,
    IconPreference,
    LLMSettings,
    PermissionGroupDef,
    RegisteredAgent,
    RegisteredTool,
    RenderedReply,
    ReplyCategory,
    ReplyField,
    ReplyIdentity,
    ReplyMode,
    ToolVisibilityFilter,
    card_with_url,
)
from ..infrastructure import A2AServer, LiteLLMClient, RedCorridorRepository
from .api import BotIconResolver, BotOwnerRegistry, DiscordMemberRef, send_rendered_reply
from .llm_tool_registration import collect_registered_tools
from .reply_sender import ReplySender

log = logging.getLogger("red.corridor")

_EventT = TypeVar("_EventT")


class CogBase:
    """Wire services once and own resources spanning the Cog lifetime."""

    bot: Red
    config: Any

    def __init__(self, bot: Red) -> None:
        self.bot = bot
        self._repository = RedCorridorRepository.create(self)
        self.config = self._repository.config
        self._permission_service = PermissionService(BotOwnerRegistry(bot))
        self._reply_service = ReplyService(BotIconResolver(bot))
        self._event_bus = EventBusService()
        self._tool_registry = ToolRegistryService()
        self._agent_directory = AgentDirectoryService()
        self._a2a_server = A2AServer(logger=log)
        # No eager start() here -- most cog_load sequences never touch the
        # LLM at all, so the session opens lazily on first actual use
        # (matches pico's original lifecycle before this moved here).
        self._llm_client = LiteLLMClient(logger=log)
        self._dependents: set[str] = set()

    async def cog_load(self) -> None:
        """Starts corridor's one shared A2A listener -- see
        docs/agent-directory-design.md. Independent of whether any agent
        has registered yet, same "the capability exists with zero
        consumers" shape the tool registry/event bus already have."""

        error = await self._start_a2a_server()
        if error is not None:
            await self._notify_owners_a2a_failed(error)

    async def cog_unload(self) -> None:
        """Cascade-unload every cog that registered itself as depending on
        corridor -- otherwise they'd keep running with a stale/missing
        corridor reference instead of failing loudly."""

        await self._a2a_server.stop()
        await self._llm_client.close()
        dependents, self._dependents = self._dependents, set()
        for extension_name in dependents:
            try:
                await self.bot.unload_extension(extension_name)
            except Exception:
                log.exception("Failed to cascade-unload dependent cog %r", extension_name)

    async def _start_a2a_server(self) -> str | None:
        """Returns None on success, or an error message on failure --
        never raises (see A2AServer.start's own docstring). Passes the
        directory's current contents so a host/port change
        (`[p]corridor a2a host/port`) re-mounts every already-registered
        agent instead of losing them."""

        settings = await self._repository.a2a_settings()
        return await self._a2a_server.start(
            host=settings.a2a_host,
            port=settings.a2a_port,
            agents=self._agent_directory.list_agents(),
        )

    async def _notify_owners_a2a_failed(self, error: str) -> None:
        """Best-effort DM -- must never raise: a missing/unreachable owner
        DM is not a reason to fail corridor's own load."""

        message = (
            f"⚠️ corridor's A2A listener failed to start ({error}). "
            "corridor is still loaded and its Discord commands work, but no "
            "registered agent (architect, or any other) is reachable over "
            "A2A until this is fixed -- try [p]corridor a2a host/port once "
            "the issue is resolved."
        )
        try:
            await self.bot.send_to_owners(message)
        except Exception:
            log.exception("corridor: could not notify owners about the A2A listener failure")

    # --- dependent-cog registration, used by dependency_loader.py -------------

    def register_dependent(self, extension_name: str) -> None:
        """Track a cog that depends on corridor, so unloading corridor
        cascades to unload it too instead of leaving it silently broken."""

        self._dependents.add(extension_name)

    def unregister_dependent(self, extension_name: str) -> None:
        self._dependents.discard(extension_name)

    # --- public cross-cog API -------------------------------------------------

    async def guild_settings(self, guild_id: int) -> GuildSettings:
        return await self._repository.guild_settings(guild_id)

    # --- Shared LLM connection, used by pico and architect ---------------------

    async def llm_settings(self) -> LLMSettings:
        """The one shared LLM connection every LLM-backed dependent reads --
        see docs/architect-design.md's LLM provider migration section."""

        return await self._repository.llm_settings()

    def llm_client(self) -> LiteLLMClient:
        """One shared client for corridor's own Cog lifetime, started
        lazily on first use and closed in `cog_unload`."""

        return self._llm_client

    async def capabilities_satisfy(self, member: discord.Member, group_key: str) -> bool:
        settings = await self._repository.guild_settings(member.guild.id)
        return await self._permission_service.satisfies(
            DiscordMemberRef(member), settings.permissions, group_key
        )

    async def require_permission(self, ctx: commands.Context, group_key: str) -> bool:
        if await self.capabilities_satisfy(ctx.author, group_key):
            return True
        await ctx.send("You don't have permission to do that.")
        return False

    async def render_reply(
        self,
        ctx: commands.Context,
        *,
        title: str | None = None,
        description: str | None = None,
        content: str | None = None,
        fields: Sequence[ReplyField] = (),
        code: Sequence[str] = (),
        identity: ReplyIdentity | None = None,
        footer_override: FooterOverride | None = None,
        category: ReplyCategory | None = None,
    ) -> RenderedReply:
        """Render title/description/content -- plus any embed `fields`
        (name/value/inline, discord.Embed.add_field-shaped) -- against a
        guild's `ReplyMode` without sending anything. `fields` render as
        structured embed fields in ReplyMode.EMBED, or as extra
        "**name:** value" text lines in ReplyMode.TEXT (see
        ReplyService.render); this is the single place that decision is
        made, so a cog that wants a rich multi-field reply -- not just a
        title/description -- still gets exactly one send call and still
        respects ReplyMode, instead of hand-building its own discord.Embed.

        Takes `ctx` (not a bare `guild_id`) so this can resolve the guild
        *and* `ctx.clean_prefix` itself: any literal `[p]` in
        `title`/`description`/`content`/every field value/every `code`
        entry is replaced with the invoking command's real prefix -- Red
        only substitutes `[p]` in command docstrings, never in reply text
        this codebase builds by hand, and that substitution is corridor's
        job alone, not something every caller should have to remember to
        do itself. `code` holds copy-pastable strings (a command, a config
        value, ...) that render in their own fenced Discord code block --
        giving the client's native copy button -- instead of inline prose;
        use `ReplyField(..., code=True)` for a whole field's value that
        should render the same way.

        The single source of truth other cogs use when they need their own
        interaction-aware dispatch (ephemeral responses, hybrid-command
        followups, ...) instead of `send_reply`'s plain `ctx.send`. See
        floorplan's (or pixelagents') `ReplyMixin` for that use.

        `identity`/`footer_override` are almost never passed here directly
        -- prefer `reply_sender()`'s bound object, which supplies
        `identity` (and `category`) automatically. `footer_override` is
        `ConsultAgentTool`'s one use case (the *consulted* agent's identity,
        not the caller's own) -- see docs/reply-identity-design.md.
        `category` picks this embed's accent color from the shared
        Agent/Room/Furniture scheme (docs/embed-colors.md); `None` (the
        default) leaves Discord's own gray, deliberately independent of
        `identity` -- a cog can have an author name with no category color,
        or vice versa."""

        assert ctx.guild is not None, "render_reply needs a guild context"
        settings = await self._repository.guild_settings(ctx.guild.id)
        return await self._reply_service.render(
            ctx.guild.id,
            settings.reply,
            ReplyContent(
                title=title,
                description=description,
                content=content,
                fields=tuple(fields),
                code=tuple(code),
            ),
            prefix=ctx.clean_prefix,
            identity=identity,
            footer_override=footer_override,
            category=category,
        )

    async def send_reply(
        self,
        ctx: commands.Context,
        *,
        title: str | None = None,
        description: str | None = None,
        content: str | None = None,
        fields: Sequence[ReplyField] = (),
        code: Sequence[str] = (),
        identity: ReplyIdentity | None = None,
        footer_override: FooterOverride | None = None,
        category: ReplyCategory | None = None,
    ) -> discord.Message:
        rendered = await self.render_reply(
            ctx,
            title=title,
            description=description,
            content=content,
            fields=fields,
            code=code,
            identity=identity,
            footer_override=footer_override,
            category=category,
        )
        return await send_rendered_reply(ctx, rendered)

    def reply_sender(
        self,
        *,
        owner: str,
        avatar_path: Path | None = None,
        category: ReplyCategory | None = None,
    ) -> ReplySender:
        """A per-cog bound sender, obtained once (typically in the calling
        cog's own `cog_load`, alongside `register_dependent`/
        `register_agent`) and reused at every one of that cog's own
        `send_reply`/`render_reply` call sites -- so `owner`/`avatar_path`/
        `category` never needs repeating as an argument at any of them. See
        docs/reply-identity-design.md.

        `avatar_path` should be the cog's *conventional* asset path
        (`<cog_package>/assets/avatar.png`) regardless of whether that
        file currently exists -- existence is checked fresh on every send
        (`build_reply_payload`), so dropping a real image there later
        needs no code change.

        `category` (see docs/embed-colors.md) is `None` by default --
        Discord's own gray -- for any cog that doesn't fit the shared
        Agent/Room/Furniture scheme, rather than guessing a bucket for it."""

        return ReplySender(self, owner=owner, avatar_path=avatar_path, category=category)

    async def default_prefix(self) -> str:
        """The bot's global default command prefix -- what Red resolves DM
        commands against, since a DM has no guild to pull a per-guild
        prefix override from. For guild-scoped reply text, prefer
        `render_reply`/`send_reply`'s automatic `ctx.clean_prefix`
        substitution instead; this exists for proactive, ctx-less
        notifications like `Red.send_to_owners` DMs."""

        prefixes = await self.bot.get_valid_prefixes()
        return prefixes[0] if prefixes else "[p]"

    async def substitute_default_prefix(self, text: str) -> str:
        """Replace literal `[p]` in `text` with `default_prefix()`.

        For text that has no `ctx` to resolve a guild-scoped prefix from --
        e.g. a proactive bot-owner DM built by a cog's own infrastructure
        layer, which shouldn't need to know how to resolve a prefix at all.
        Prefix resolution/substitution is corridor's job everywhere, not
        something every caller re-derives for itself."""

        return text.replace("[p]", await self.default_prefix())

    # --- Pub/Sub: corridor's Discord-vocabulary event bus ----------------------

    async def publish_event(self, event: object) -> None:
        """Publish a corridor Discord-vocabulary event (`AgentReplied`,
        `AgentPresenceChanged`, ...) to every subscriber registered for its
        concrete type. See `EventBusService.publish` for delivery semantics
        (synchronous, awaited dispatch, per-subscriber error isolation)."""

        await self._event_bus.publish(event)

    def subscribe_event(
        self,
        event_type: type[_EventT],
        handler: Callable[[_EventT], Awaitable[None]],
        *,
        owner: str,
    ) -> None:
        """Register interest in `event_type`, called from the subscriber's
        own `cog_load`. `owner` should be the subscribing cog's class name
        (matching `register_dependent`'s convention) -- `unsubscribe_owner`
        drops every handler `owner` registered, across every event type, in
        one call."""

        self._event_bus.subscribe(event_type, handler, owner=owner)

    def unsubscribe_owner(self, owner: str) -> None:
        """Call from the subscriber's own `cog_unload` -- corridor does not
        track/cascade a subscriber's lifecycle the way `register_dependent`
        does the reverse direction for a dependent cog."""

        self._event_bus.unsubscribe_owner(owner)

    @commands.Cog.listener()
    async def on_cog_remove(self, cog: commands.Cog) -> None:
        """Defensive: Red dispatches this unconditionally after every cog
        removal, even if that cog's own cog_unload() raised partway through
        (discord.py's Cog._eject swallows that exception before dispatching,
        and the cog is already popped from bot.cogs by then either way) --
        so a subscriber that crashes mid-unload without ever reaching its
        own unsubscribe_owner() call doesn't leak a stale subscription
        forever. register_dependent's cascade exists for the same distrust
        in the opposite (corridor-unloads-first) direction."""

        self._event_bus.unsubscribe_owner(cog.qualified_name)
        self._tool_registry.unregister_owner(cog.qualified_name)
        self._tool_registry.unregister_visibility_filter_owner(cog.qualified_name)
        self._agent_directory.unregister_owner(cog.qualified_name)
        self._a2a_server.rebuild_routes(self._agent_directory.list_agents())

    # --- Cross-cog LLM tool registry -------------------------------------------

    def register_tool(self, tool: RegisteredTool, *, owner: str) -> None:
        """Register `tool` for cross-cog discovery -- called from the
        registering cog's own `cog_load`. `owner` should be that cog's
        class name (matching `subscribe_event`'s convention), so
        `on_cog_remove`'s defensive cleanup above lines up with it.

        The lower-level primitive: prefer `register_llm_tools` for a tool
        that's really just a `@domain.llm_tool`-decorated command -- this
        one is for a `RegisteredTool` built by hand (not backed by any
        Discord command at all)."""

        self._tool_registry.register(tool, owner=owner)

    def register_llm_tools(self, cog: object, *, owner: str) -> None:
        """Scan `cog` for every command decorated with `@domain.llm_tool`
        and register each one -- called from the registering cog's own
        `cog_load`, same `owner` convention as `register_tool` above."""

        for tool in collect_registered_tools(cog):
            self.register_tool(tool, owner=owner)

    def unregister_tool_owner(self, owner: str) -> None:
        """Call from the registering cog's own `cog_unload` -- corridor
        does not track/cascade a registrant's lifecycle the way
        `register_dependent` does the reverse direction for a dependent
        cog."""

        self._tool_registry.unregister_owner(owner)

    def unregister_tool(self, name: str) -> None:
        """Remove one tool by name, regardless of owner -- for a registrant
        managing several tools under one owner that needs to drop a single
        one (see ToolRegistryService.unregister). A no-op if `name` isn't
        registered."""

        self._tool_registry.unregister(name)

    def register_tool_visibility_filter(
        self, predicate: ToolVisibilityFilter, *, owner: str
    ) -> None:
        """Install `predicate` as an additional gate `list_tools_for`
        evaluates for every tool, alongside `required_group`/
        `availability_check` -- called from the installing cog's own
        `cog_load`, same `owner` convention as `register_tool`. Intended
        for exactly one installer today (toolbox's enable/disable +
        per-guild-override state, see
        docs/toolbox-command-tool-toggle-design.md) but supports several;
        a tool must pass every installed filter to remain visible. No
        filter installed at all means no behavior change -- every tool
        that already passes the existing checks stays visible."""

        self._tool_registry.register_visibility_filter(predicate, owner=owner)

    def unregister_visibility_filter_owner(self, owner: str) -> None:
        """Call from the installing cog's own `cog_unload` -- same
        convention as `unregister_tool_owner`."""

        self._tool_registry.unregister_visibility_filter_owner(owner)

    def list_tools(self) -> tuple[RegisteredTool, ...]:
        """Every registered tool, unfiltered by permission. Prefer
        `list_tools_for` when the caller has an invoking context to check
        against."""

        return self._tool_registry.list_tools()

    async def list_tools_for(self, ctx: commands.Context) -> tuple[RegisteredTool, ...]:
        """Every registered tool `ctx.author` is allowed to invoke.

        Explicit corridor permission groups and inferred Discord command
        checks are both evaluated here, before a consumer exposes tools to
        an LLM. A failing or broken command check omits only that tool.
        """

        allowed: list[RegisteredTool] = []
        for tool in self._tool_registry.list_tools():
            if tool.required_group is not None and not await self.capabilities_satisfy(
                cast(discord.Member, ctx.author), tool.required_group
            ):
                continue
            if tool.availability_check is not None:
                try:
                    if not await tool.availability_check(ctx):
                        continue
                except Exception:
                    log.warning(
                        "corridor: availability check failed for LLM tool %r; omitting it",
                        tool.name,
                        exc_info=True,
                    )
                    continue
            if not await self._passes_visibility_filters(ctx, tool):
                continue
            allowed.append(tool)
        return tuple(allowed)

    async def _passes_visibility_filters(self, ctx: commands.Context, tool: RegisteredTool) -> bool:
        for predicate in self._tool_registry.list_visibility_filters():
            try:
                if not await predicate(ctx, tool):
                    return False
            except Exception:
                log.warning(
                    "corridor: visibility filter failed for LLM tool %r; omitting it",
                    tool.name,
                    exc_info=True,
                )
                return False
        return True

    # --- Cross-cog A2A agent directory, mounted on corridor's shared listener ---

    async def register_agent(self, agent: RegisteredAgent, *, owner: str) -> None:
        """Register `agent` for cross-cog A2A discovery -- called from the
        registering cog's own `cog_load`, once its `AgentCard`/
        `AgentExecutor` are built. `owner` should be that cog's class
        name, matching `subscribe_event`'s convention.

        Overwrites `agent.card`'s one `supported_interfaces[0].url` (and
        `icon_url`, when `agent.avatar_path` is set) with corridor's own
        configured host/port plus `/<agent_key>/` before storing it --
        the registering agent has no way to know what host/port it will
        ultimately be reachable at, since it no longer binds a listener
        of its own. Async (unlike `register_tool`) because it needs
        corridor's own current A2A settings to build that URL. Also
        rebuilds the live route table, so the new agent (and its avatar
        route, if any -- see docs/reply-identity-design.md section 7) is
        reachable immediately, not just on corridor's next `cog_load`."""

        settings = await self._repository.a2a_settings()
        base = f"http://{settings.a2a_host}:{settings.a2a_port}/{agent.agent_key}/"
        icon_url = f"{base}avatar.png" if agent.avatar_path is not None else None
        rewritten = RegisteredAgent(
            agent_key=agent.agent_key,
            card=card_with_url(agent.card, base, icon_url=icon_url),
            executor=agent.executor,
            avatar_path=agent.avatar_path,
        )
        self._agent_directory.register(rewritten, owner=owner)
        self._a2a_server.rebuild_routes(self._agent_directory.list_agents())

    def unregister_agent_owner(self, owner: str) -> None:
        """Call from the registering cog's own `cog_unload` -- corridor
        does not track/cascade a registrant's lifecycle the way
        `register_dependent` does the reverse direction for a dependent
        cog."""

        self._agent_directory.unregister_owner(owner)
        self._a2a_server.rebuild_routes(self._agent_directory.list_agents())

    def unregister_agent(self, agent_key: str) -> None:
        """Remove one agent by key, regardless of owner. A no-op if
        `agent_key` isn't registered."""

        self._agent_directory.unregister(agent_key)
        self._a2a_server.rebuild_routes(self._agent_directory.list_agents())

    def list_agents(self) -> tuple[RegisteredAgent, ...]:
        """Every currently registered agent -- pico calls this once per
        turn to build one `consult_<agent_key>` tool per entry. See
        docs/agent-directory-design.md."""

        return self._agent_directory.list_agents()

    # --- settings mutation, used by settings_ui.py and [p]corridor commands ---

    async def a2a_settings(self) -> A2ASettings:
        return await self._repository.a2a_settings()

    async def set_a2a_host(self, value: str) -> str | None:
        """Sets the bind host and live-restarts corridor's shared A2A
        listener, re-mounting every already-registered agent. Returns an
        error string on a bind failure, None on success -- same
        never-raise convention as `_start_a2a_server`."""

        await self._repository.set_a2a_host(value)
        return await self._start_a2a_server()

    async def set_a2a_port(self, value: int) -> str | None:
        await self._repository.set_a2a_port(value)
        return await self._start_a2a_server()

    async def set_llm_base_url(self, value: str) -> None:
        await self._repository.set_llm_base_url(value)

    async def set_llm_api_key(self, value: str) -> None:
        await self._repository.set_llm_api_key(value)

    async def set_llm_model(self, value: str) -> None:
        await self._repository.set_llm_model(value)

    async def set_reply_mode(self, guild_id: int, mode: ReplyMode) -> None:
        await self._repository.set_reply_mode(guild_id, mode)

    async def set_show_timestamp(self, guild_id: int, value: bool) -> None:
        await self._repository.set_show_timestamp(guild_id, value)

    async def set_footer_text(self, guild_id: int, text: str | None) -> None:
        await self._repository.set_footer_text(guild_id, text)

    async def set_icon_preference(self, guild_id: int, icon: IconPreference) -> None:
        await self._repository.set_icon_preference(guild_id, icon)

    async def list_permission_groups(self, guild_id: int) -> tuple[PermissionGroupDef, ...]:
        return await self._repository.list_permission_groups(guild_id)

    async def add_permission_group(
        self,
        guild_id: int,
        key: str,
        label: str,
        role_ids: frozenset[int] = frozenset(),
        permission_names: frozenset[str] = frozenset(),
    ) -> None:
        await self._repository.add_permission_group(
            guild_id, key, label, role_ids, permission_names
        )

    async def remove_permission_group(self, guild_id: int, key: str) -> None:
        await self._repository.remove_permission_group(guild_id, key)

    async def set_group_role_ids(self, guild_id: int, key: str, role_ids: frozenset[int]) -> None:
        await self._repository.set_group_role_ids(guild_id, key, role_ids)

    async def set_group_permissions(
        self, guild_id: int, key: str, permission_names: frozenset[str]
    ) -> None:
        await self._repository.set_group_permissions(guild_id, key, permission_names)

    async def set_group_label(self, guild_id: int, key: str, label: str) -> None:
        await self._repository.set_group_label(guild_id, key, label)

    async def set_owner_label(self, guild_id: int, label: str) -> None:
        await self._repository.set_owner_label(guild_id, label)

    async def set_employee_label(self, guild_id: int, label: str) -> None:
        await self._repository.set_employee_label(guild_id, label)
