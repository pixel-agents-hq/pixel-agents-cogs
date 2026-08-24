"""Bulk Discord guild sync helpers -- a direct OfficeService call, never
routed through corridor's Pub/Sub bus (see `_full_sync`'s own docstring
for why). The per-event listeners this module used to also own
(on_member_update/on_presence_update/on_member_join/on_member_remove/
on_message, each normalizing one raw discord.py event into a corridor
event and publishing it) moved to `corridor/adapters/discord_gateway.py`
-- floorplan is a pure subscriber now, see docs/corridor-pubsub-design.md.
`event_subscriptions.py` is the (only) subscriber that translates a
published event back into the snapshot types OfficeService/PresenceService
expect, and drives them.
"""

from __future__ import annotations

import discord

from pixelagents.domain import AgentSnapshot

from ..infrastructure.discord import member_snapshot
from .cog_base import PixelAgentsBase, log

VISIBLE_STATUSES = {"online", "idle", "dnd"}


class DiscordGatewayMixin(PixelAgentsBase):
    """Bulk sync helpers only -- see this module's own docstring for why
    the per-event listeners that used to live here moved to corridor."""

    async def _sync_all_guilds(self) -> None:
        for guild in self.bot.guilds:
            if await self._settings_repository.guild_enabled(guild):
                try:
                    await self._full_sync(guild)
                except Exception as exc:
                    log.error("floorplan: sync error for guild %s: %s", guild.id, exc)

    async def _full_sync(self, guild: discord.Guild) -> str:
        # A bulk guild resync, not a per-member Discord *event* -- stays a
        # direct OfficeService call. Threading potentially hundreds of
        # members through the bus per full sync isn't what "event volume
        # bounded by Discord message/interaction rates" (see
        # docs/corridor-pubsub-design.md) was ever about.
        guild_settings = await self._settings_repository.guild_settings(guild)
        rich_presence_enabled = await self._settings_repository.broadcast_rich_presence()
        snapshots = tuple(self._member_snapshot(member) for member in guild.members)
        return await self._universes.get_or_create(guild.id).office.sync_guild(
            guild.id,
            snapshots,
            include_bots=guild_settings.include_bots,
            rich_presence_enabled=rich_presence_enabled,
        )

    def _member_snapshot(self, member: discord.Member) -> AgentSnapshot:
        bot_user_id = self.bot.user.id if self.bot.user is not None else None
        return member_snapshot(member, bot_user_id=bot_user_id)

    def _status_str(self, member: discord.Member) -> str | None:
        status = self._member_snapshot(member).status
        return status.value if status is not None else None

    def _is_included(self, member: discord.Member, include_bots: bool) -> bool:
        return not (self._member_snapshot(member).is_bot and not include_bots)

    def _agent_status(self, member: discord.Member) -> str:
        return self._universes.get_or_create(member.guild.id).presence.agent_status(
            self._member_snapshot(member)
        )

    async def _despawn_guild(self, guild: discord.Guild) -> None:
        # Clears this guild's tracked agents; the (now-empty) OfficeService
        # itself stays in the registry -- `cmd_despawnall` reuses this same
        # path without disabling the guild, so tearing the universe down
        # here would just force an immediate, wasteful recreation.
        await self._universes.get_or_create(guild.id).office.despawn_guild(guild.id)
