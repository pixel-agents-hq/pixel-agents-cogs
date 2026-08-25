"""Dependency composition and the public cross-cog API surface.

Other cogs call these methods via `bot.get_cog("Corridor")` -- this is the
stable contract they depend on through `required_cogs`.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Sequence
from typing import Any, TypeVar, cast

import discord
from redbot.core import commands
from redbot.core.bot import Red

from ..application import (
    EventBusService,
    PermissionService,
    ReplyContent,
    ReplyService,
    ToolRegistryService,
)
from ..domain import (
    GuildSettings,
    IconPreference,
    PermissionGroupDef,
    RegisteredTool,
    RenderedReply,
    ReplyField,
    ReplyMode,
    ToolVisibilityFilter,
)
from ..infrastructure import RedCorridorRepository
from .api import BotIconResolver, BotOwnerRegistry, DiscordMemberRef, send_rendered_reply
from .llm_tool_registration import collect_registered_tools

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
        self._dependents: set[str] = set()

    async def cog_load(self) -> None:
        """Extension point for start-up work."""

    async def cog_unload(self) -> None:
        """Cascade-unload every cog that registered itself as depending on
        corridor -- otherwise they'd keep running with a stale/missing
        corridor reference instead of failing loudly."""

        dependents, self._dependents = self._dependents, set()
        for extension_name in dependents:
            try:
                await self.bot.unload_extension(extension_name)
            except Exception:
                log.exception("Failed to cascade-unload dependent cog %r", extension_name)

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
        floorplan's (or pixelagents') `ReplyMixin` for that use."""

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
    ) -> discord.Message:
        rendered = await self.render_reply(
            ctx,
            title=title,
            description=description,
            content=content,
            fields=fields,
            code=code,
        )
        return await send_rendered_reply(ctx, rendered)

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

    # --- settings mutation, used by settings_ui.py and [p]corridor commands ---

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
