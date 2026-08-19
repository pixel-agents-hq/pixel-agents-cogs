"""Dependency composition and the public cross-cog API surface.

Other cogs call these methods via `bot.get_cog("Corridor")` -- this is the
stable contract they depend on through `required_cogs`.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

import discord
from redbot.core import commands
from redbot.core.bot import Red

from ..application import PermissionService, ReplyContent, ReplyService
from ..domain import (
    GuildSettings,
    IconPreference,
    PermissionGroupDef,
    RenderedReply,
    ReplyField,
    ReplyMode,
)
from ..infrastructure import RedCorridorRepository
from .api import BotIconResolver, BotOwnerRegistry, DiscordMemberRef, send_rendered_reply

log = logging.getLogger("red.corridor")


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
        guild_id: int,
        *,
        title: str | None = None,
        description: str | None = None,
        content: str | None = None,
        fields: Sequence[ReplyField] = (),
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

        The single source of truth other cogs use when they need their own
        interaction-aware dispatch (ephemeral responses, hybrid-command
        followups, ...) instead of `send_reply`'s plain `ctx.send`. See
        pixelagents' `ReplyMixin` for that use."""

        settings = await self._repository.guild_settings(guild_id)
        return await self._reply_service.render(
            guild_id,
            settings.reply,
            ReplyContent(
                title=title, description=description, content=content, fields=tuple(fields)
            ),
        )

    async def send_reply(
        self,
        ctx: commands.Context,
        *,
        title: str | None = None,
        description: str | None = None,
        content: str | None = None,
        fields: Sequence[ReplyField] = (),
    ) -> discord.Message:
        assert ctx.guild is not None, "send_reply needs a guild context"
        rendered = await self.render_reply(
            ctx.guild.id, title=title, description=description, content=content, fields=fields
        )
        return await send_rendered_reply(ctx, rendered)

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
        self, guild_id: int, key: str, label: str, role_ids: frozenset[int] = frozenset()
    ) -> None:
        await self._repository.add_permission_group(guild_id, key, label, role_ids)

    async def remove_permission_group(self, guild_id: int, key: str) -> None:
        await self._repository.remove_permission_group(guild_id, key)

    async def set_group_role_ids(self, guild_id: int, key: str, role_ids: frozenset[int]) -> None:
        await self._repository.set_group_role_ids(guild_id, key, role_ids)

    async def set_group_label(self, guild_id: int, key: str, label: str) -> None:
        await self._repository.set_group_label(guild_id, key, label)

    async def set_owner_label(self, guild_id: int, label: str) -> None:
        await self._repository.set_owner_label(guild_id, label)

    async def set_employee_label(self, guild_id: int, label: str) -> None:
        await self._repository.set_employee_label(guild_id, label)
