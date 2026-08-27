"""Corridor's own Discord gateway listeners: normalize raw discord.py
events into corridor's Discord-vocabulary Pub/Sub bus.

Moved here from floorplan's own `discord_gateway.py` -- see
docs/corridor-pubsub-design.md. Publishes unconditionally, for every
guild and every member: no guild-enabled/include_bots/office-tracking/
broadcast-toggle gating, unlike floorplan's old version of this module.
That filtering is now each subscriber's own concern (floorplan's
`event_subscriptions.py` applies it on the way in), not a publisher's --
corridor is a leaf package (see `corridor/info.json`'s empty
`required_cogs`) and must never depend on floorplan or pixelagents to
decide what counts as "something happened"."""

from __future__ import annotations

from typing import Any, cast

import discord
from redbot.core import commands

from ..domain.models import AgentActivity, AgentPresenceChanged, AgentRef, AgentReplied
from .cog_base import CogBase, log

_ACTIVITY_KINDS = {
    discord.ActivityType.playing: "playing",
    discord.ActivityType.streaming: "streaming",
    discord.ActivityType.listening: "listening",
    discord.ActivityType.watching: "watching",
    discord.ActivityType.custom: "custom",
    discord.ActivityType.competing: "competing",
}

_PRESENCE_STATUSES = {"online", "idle", "dnd", "offline"}


def _optional_text(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _activity(activity: Any) -> AgentActivity | None:
    kind = _ACTIVITY_KINDS.get(cast(discord.ActivityType, getattr(activity, "type", None)))
    if kind is None:
        return None
    return AgentActivity(
        kind=kind,
        name=_optional_text(getattr(activity, "name", None)),
        details=_optional_text(getattr(activity, "details", None)),
        state=_optional_text(getattr(activity, "state", None)),
        title=_optional_text(getattr(activity, "title", None)),
        artist=_optional_text(getattr(activity, "artist", None)),
    )


def _presence_event(member: discord.Member, *, bot_user_id: int | None) -> AgentPresenceChanged:
    raw_status = "online" if member.id == bot_user_id else str(member.status)
    status = raw_status if raw_status in _PRESENCE_STATUSES else "offline"
    activities = tuple(
        snapshot for activity in member.activities if (snapshot := _activity(activity)) is not None
    )
    return AgentPresenceChanged(
        agent=AgentRef(discord_user_id=member.id, guild_id=member.guild.id, is_bot=member.bot),
        display_name=member.display_name,
        status=status,  # type: ignore[arg-type]
        activities=activities,
    )


class DiscordGatewayMixin(CogBase):
    """Normalize Discord events, then publish unconditionally -- deciding
    what's worth rendering stays entirely a subscriber's job."""

    def _bot_user_id(self) -> int | None:
        return self.bot.user.id if self.bot.user is not None else None

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member) -> None:
        if before.display_name == after.display_name:
            return
        try:
            await self.publish_event(_presence_event(after, bot_user_id=self._bot_user_id()))
        except Exception as exc:
            log.error("corridor: on_member_update error for %s: %s", after.id, exc)

    @commands.Cog.listener()
    async def on_presence_update(self, before: discord.Member, after: discord.Member) -> None:
        if before.status == after.status and before.activities == after.activities:
            return
        try:
            await self.publish_event(_presence_event(after, bot_user_id=self._bot_user_id()))
        except Exception as exc:
            log.error("corridor: on_presence_update error for %s: %s", after.id, exc)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        try:
            await self.publish_event(_presence_event(member, bot_user_id=self._bot_user_id()))
        except Exception as exc:
            log.error("corridor: on_member_join error for %s: %s", member.id, exc)

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        try:
            await self.publish_event(
                AgentPresenceChanged(
                    agent=AgentRef(
                        discord_user_id=member.id, guild_id=member.guild.id, is_bot=member.bot
                    ),
                    display_name=member.display_name,
                    status="offline",
                )
            )
        except Exception as exc:
            log.error("corridor: on_member_remove error for %s: %s", member.id, exc)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        guild = message.guild
        if guild is None:
            return
        if self.bot.user is not None and message.author.id == self.bot.user.id:
            # This bot's own message -- e.g. pico's ReplyTool already
            # publishes AgentReplied for its own reply directly. Without
            # this guard, corridor would see that same outgoing message
            # and publish a second, duplicate AgentReplied for it. Other
            # bots' messages are unaffected -- they keep publishing.
            return
        try:
            await self.publish_event(
                AgentReplied(
                    agent=AgentRef(
                        discord_user_id=message.author.id,
                        guild_id=guild.id,
                        is_bot=message.author.bot,
                    ),
                    # clean_content, not content -- resolves mentions to
                    # readable @name/@role/#channel text.
                    summary=message.clean_content or "",
                )
            )
        except Exception as exc:
            log.error("corridor: on_message error for %s: %s", message.id, exc)


__all__ = ["DiscordGatewayMixin"]
