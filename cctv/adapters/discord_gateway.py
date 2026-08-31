"""Bulk Discord guild sync for the Discord pipeline's roster -- a direct
`OfficeService` call, never routed through corridor's Pub/Sub bus (see
floorplan's former identically-shaped module, now retired there, for why:
threading potentially hundreds of members through the bus per full sync
isn't what "event volume bounded by Discord message/interaction rates"
was ever about). Per-event listeners live in corridor itself
(`corridor/adapters/discord_gateway.py`) and reach this pipeline's roster
via `event_subscriptions_discord.py`, not this module.
"""

from __future__ import annotations

import discord

from pixelagents.domain import AgentSnapshot

from ..infrastructure.discord import member_snapshot
from .cog_base import CogBase, log


class DiscordGatewayMixin(CogBase):
    """Requires `self.bot`, `self._repository`, `self._discord_office_service`
    (all provided by `CogBase`)."""

    async def _sync_all_guilds(self) -> None:
        for guild in self.bot.guilds:
            if await self._repository.guild_enabled(guild.id):
                try:
                    await self._full_sync(guild)
                except Exception as exc:
                    log.error("cctv: sync error for guild %s: %s", guild.id, exc)

    async def _full_sync(self, guild: discord.Guild) -> str:
        guild_settings = await self._repository.guild_settings(guild.id)
        global_settings = await self._repository.global_settings()
        snapshots = tuple(self._member_snapshot(member) for member in guild.members)
        return await self._discord_office_service.sync_guild(
            guild.id,
            snapshots,
            include_bots=guild_settings.include_bots,
            rich_presence_enabled=global_settings.broadcast_rich_presence,
        )

    def _member_snapshot(self, member: discord.Member) -> AgentSnapshot:
        bot_user_id = self.bot.user.id if self.bot.user is not None else None
        return member_snapshot(member, bot_user_id=bot_user_id)

    async def _despawn_guild(self, guild: discord.Guild) -> None:
        await self._discord_office_service.despawn_guild(guild.id)

    async def _initial_sync(self) -> None:
        """Scheduled from `cog_load` as a background task -- waits for
        Red's own gateway connection to settle before doing a bulk scan,
        the same ordering floorplan's own former `_initial_sync` used."""

        await self.bot.wait_until_red_ready()
        await self._sync_all_guilds()

    async def cog_load(self) -> None:
        await super().cog_load()
        self._create_background_task(self._initial_sync(), name="cctv-initial-discord-sync")


__all__ = ["DiscordGatewayMixin"]
