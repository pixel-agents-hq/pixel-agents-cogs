"""Discord gateway listeners and compatibility snapshot helpers."""

from __future__ import annotations

import asyncio

import discord
from redbot.core import commands

from ..domain import AgentKey, AgentSnapshot, PresenceStatus
from ..infrastructure.discord import member_snapshot, message_snapshot
from .cog_base import PixelAgentsBase, log

VISIBLE_STATUSES = {"online", "idle", "dnd"}


class DiscordGatewayMixin(PixelAgentsBase):
    """Normalize Discord events before invoking office application policy."""

    async def _sync_all_guilds(self) -> None:
        for guild in self.bot.guilds:
            if await self._settings_repository.guild_enabled(guild):
                try:
                    await self._full_sync(guild)
                except Exception as exc:
                    log.error("floorplan: sync error for guild %s: %s", guild.id, exc)

    async def _full_sync(self, guild: discord.Guild) -> str:
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

    @staticmethod
    def _pick_presence_activity(
        member: discord.Member,
    ) -> (
        discord.Activity
        | discord.Game
        | discord.CustomActivity
        | discord.Streaming
        | discord.Spotify
        | None
    ):
        activities = [
            activity
            for activity in member.activities
            if activity.type != discord.ActivityType.custom
        ]
        listening = next(
            (
                activity
                for activity in activities
                if activity.type == discord.ActivityType.listening
            ),
            None,
        )
        return listening or (activities[0] if activities else None)

    def _build_presence_label(self, member: discord.Member) -> str | None:
        return self._presence_service.label(self._member_snapshot(member))

    def _status_str(self, member: discord.Member) -> str | None:
        status = self._member_snapshot(member).status
        return status.value if status is not None else None

    def _is_included(self, member: discord.Member, include_bots: bool) -> bool:
        return not (self._member_snapshot(member).is_bot and not include_bots)

    def _has_rich_presence(self, member: discord.Member) -> bool:
        return self._presence_service.agent_status(self._member_snapshot(member)) == "active"

    def _agent_status(self, member: discord.Member) -> str:
        return self._presence_service.agent_status(self._member_snapshot(member))

    async def _reconcile_member(self, member: discord.Member, include_bots: bool) -> None:
        await self._office_service.reconcile(
            self._member_snapshot(member),
            include_bots=include_bots,
            rich_presence_enabled=await self._settings_repository.broadcast_rich_presence(),
        )

    def _is_user_active_in_other_guild(self, guild_id: int, user_id: int) -> bool:
        return self._office_service.is_user_active_in_other_guild(guild_id, user_id)

    async def _spawn_agent(
        self,
        guild_id: int,
        user_id: int,
        name: str,
        folder: str,
        member: discord.Member,
    ) -> None:
        original = self._member_snapshot(member)
        snapshot = AgentSnapshot(
            key=AgentKey(guild_id, user_id),
            display_name=name,
            status=PresenceStatus(folder),
            is_bot=original.is_bot,
            activities=original.activities,
        )
        await self._office_service.spawn(
            snapshot,
            rich_presence_enabled=await self._settings_repository.broadcast_rich_presence(),
        )

    async def _close_agent(self, guild_id: int, user_id: int) -> None:
        await self._office_service.close(AgentKey(guild_id, user_id))

    async def _despawn_guild(self, guild: discord.Guild) -> None:
        await self._office_service.despawn_guild(guild.id)

    async def _send_presence_tool(self, agent_id: int, label: str) -> None:
        await self._presence_service.send_tool(agent_id, label)

    async def _update_presence_tool(
        self, guild_id: int, user_id: int, member: discord.Member
    ) -> None:
        original = self._member_snapshot(member)
        snapshot = AgentSnapshot(
            key=AgentKey(guild_id, user_id),
            display_name=original.display_name,
            status=original.status,
            is_bot=original.is_bot,
            activities=original.activities,
        )
        await self._presence_service.update(
            snapshot,
            self._office_service.agent_id(user_id),
            enabled=await self._settings_repository.broadcast_rich_presence(),
        )

    async def _clear_tool_after_delay(
        self, agent_id: int, delay: float, guild_id: int = 0, user_id: int = 0
    ) -> None:
        await asyncio.sleep(delay)
        if self._closing:
            return
        if guild_id and user_id:
            await self._office_service.clear_message_activity(AgentKey(guild_id, user_id))
        else:
            await self._send({"type": "agentToolsClear", "id": agent_id})

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member) -> None:
        guild_settings = await self._settings_repository.guild_settings(after.guild)
        if not guild_settings.enabled or before.display_name == after.display_name:
            return
        try:
            await self._reconcile_member(after, guild_settings.include_bots)
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
            await self._reconcile_member(after, guild_settings.include_bots)
        except Exception as exc:
            log.error("on_presence_update error for %s: %s", after.id, exc)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        guild_settings = await self._settings_repository.guild_settings(member.guild)
        if not guild_settings.enabled or self._status_str(member) is None:
            return
        try:
            await self._reconcile_member(member, guild_settings.include_bots)
        except Exception as exc:
            log.error("on_member_join error for %s: %s", member.id, exc)

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        if not await self._settings_repository.guild_enabled(member.guild):
            return
        try:
            await self._close_agent(member.guild.id, member.id)
        except Exception as exc:
            log.error("on_member_remove error for %s: %s", member.id, exc)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if self._closing:
            return
        guild = message.guild
        if guild is None:
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
        await self._office_service.send_message_activity(snapshot)
        delay = await self._settings_repository.message_tool_clear_delay()
        self._task_supervisor.create(
            self._clear_tool_after_delay(
                self._office_service.agent_id(snapshot.key.user_id),
                delay,
                snapshot.key.guild_id,
                snapshot.key.user_id,
            ),
            name=f"floorplan-message-clear-{snapshot.message_id}",
        )
