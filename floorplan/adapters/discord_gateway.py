"""Discord gateway listeners: normalize raw discord.py events into
corridor's Discord-vocabulary Pub/Sub bus.

This module no longer calls OfficeService/PresenceService directly -- it
only ever builds a corridor event and publishes it. `event_subscriptions.py`
is the (only) subscriber that translates a published event back into the
snapshot types those services expect, and drives them exactly as this
module used to -- see that module for the other half of this split.
"""

from __future__ import annotations

import discord
from redbot.core import commands

from corridor.domain import AgentActivity as CorridorActivity
from corridor.domain import AgentPresenceChanged, AgentRef, AgentReplied
from pixelagents.domain import AgentSnapshot

from ..infrastructure.discord import member_snapshot, message_snapshot
from .cog_base import PixelAgentsBase, log

VISIBLE_STATUSES = {"online", "idle", "dnd"}


def _presence_event(snapshot: AgentSnapshot) -> AgentPresenceChanged:
    """Discord's own online/idle/dnd/offline vocabulary, not pixel-agents'
    active/waiting one -- see AgentPresenceChanged's docstring. `None`
    (offline/invisible, or a member snapshot built for someone who just
    left) maps to the explicit `"offline"` value; there is no separate
    "member left" event, on_member_remove publishes this same shape."""

    status = snapshot.status.value if snapshot.status is not None else "offline"
    return AgentPresenceChanged(
        agent=AgentRef(
            discord_user_id=snapshot.key.user_id,
            guild_id=snapshot.key.guild_id,
            is_bot=snapshot.is_bot,
        ),
        display_name=snapshot.display_name,
        status=status,  # type: ignore[arg-type]
        activities=tuple(
            CorridorActivity(
                kind=activity.kind.value,
                name=activity.name,
                title=activity.title,
                artist=activity.artist,
                details=activity.details,
                state=activity.state,
            )
            for activity in snapshot.activities
        ),
    )


class DiscordGatewayMixin(PixelAgentsBase):
    """Normalize Discord events, then publish -- never drive office
    application policy directly (see event_subscriptions.py for that)."""

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
        return await self._office_service.sync_guild(
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
        return self._presence_service.agent_status(self._member_snapshot(member))

    async def _despawn_guild(self, guild: discord.Guild) -> None:
        await self._office_service.despawn_guild(guild.id)

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member) -> None:
        guild_settings = await self._settings_repository.guild_settings(after.guild)
        if not guild_settings.enabled or before.display_name == after.display_name:
            return
        try:
            await self._corridor.publish_event(_presence_event(self._member_snapshot(after)))
        except Exception as exc:
            log.error("on_member_update error for %s: %s", after.id, exc)

    @commands.Cog.listener()
    async def on_presence_update(self, before: discord.Member, after: discord.Member) -> None:
        guild_settings = await self._settings_repository.guild_settings(after.guild)
        if not guild_settings.enabled:
            return
        if before.status == after.status and before.activities == after.activities:
            return
        try:
            await self._corridor.publish_event(_presence_event(self._member_snapshot(after)))
        except Exception as exc:
            log.error("on_presence_update error for %s: %s", after.id, exc)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        guild_settings = await self._settings_repository.guild_settings(member.guild)
        if not guild_settings.enabled or self._status_str(member) is None:
            return
        try:
            await self._corridor.publish_event(_presence_event(self._member_snapshot(member)))
        except Exception as exc:
            log.error("on_member_join error for %s: %s", member.id, exc)

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        if not await self._settings_repository.guild_enabled(member.guild):
            return
        try:
            await self._corridor.publish_event(
                AgentPresenceChanged(
                    agent=AgentRef(
                        discord_user_id=member.id, guild_id=member.guild.id, is_bot=member.bot
                    ),
                    display_name=member.display_name,
                    status="offline",
                )
            )
        except Exception as exc:
            log.error("on_member_remove error for %s: %s", member.id, exc)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if self._closing:
            return
        guild = message.guild
        if guild is None:
            return
        if self.bot.user is not None and message.author.id == self.bot.user.id:
            # This bot's own message -- e.g. pico's ReplyTool just sent a
            # reply via corridor.send_reply, which pico now also publishes
            # AgentReplied for directly. Without this guard, this listener
            # would see that same outgoing message and publish a second,
            # duplicate AgentReplied for it. Other bots' messages are
            # unaffected -- they keep publishing exactly as before.
            return
        snapshot = message_snapshot(message)
        if snapshot is None:
            return
        if not await self._settings_repository.guild_enabled(guild):
            return
        if not self._office_service.is_tracked(snapshot.key):
            return
        if not await self._settings_repository.broadcast_messages():
            return
        await self._corridor.publish_event(
            AgentReplied(
                agent=AgentRef(
                    discord_user_id=snapshot.key.user_id,
                    guild_id=snapshot.key.guild_id,
                    is_bot=message.author.bot,
                ),
                summary=snapshot.content,  # untruncated -- truncation is the subscriber's job
            )
        )
