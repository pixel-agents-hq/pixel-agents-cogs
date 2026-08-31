"""Adapters from discord.py gateway objects to immutable domain snapshots.

Ported verbatim from floorplan's former `infrastructure/discord.py` (now
retired there, see docs/cctv-design.md) -- depends only on
`pixelagents.domain` and `discord.py`, never floorplan itself.
"""

from __future__ import annotations

from typing import Any, cast

import discord

from pixelagents.domain import (
    ActivityKind,
    ActivitySnapshot,
    AgentKey,
    AgentSnapshot,
    MessageSnapshot,
    PresenceStatus,
)

_ACTIVITY_KINDS = {
    discord.ActivityType.playing: ActivityKind.PLAYING,
    discord.ActivityType.streaming: ActivityKind.STREAMING,
    discord.ActivityType.listening: ActivityKind.LISTENING,
    discord.ActivityType.watching: ActivityKind.WATCHING,
    discord.ActivityType.custom: ActivityKind.CUSTOM,
    discord.ActivityType.competing: ActivityKind.COMPETING,
}


def _optional_text(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def activity_snapshot(activity: Any) -> ActivitySnapshot | None:
    activity_type = cast(discord.ActivityType, getattr(activity, "type", None))
    kind = _ACTIVITY_KINDS.get(activity_type)
    if kind is None:
        return None
    return ActivitySnapshot(
        kind=kind,
        name=_optional_text(getattr(activity, "name", None)),
        details=_optional_text(getattr(activity, "details", None)),
        state=_optional_text(getattr(activity, "state", None)),
        title=_optional_text(getattr(activity, "title", None)),
        artist=_optional_text(getattr(activity, "artist", None)),
    )


def member_snapshot(member: discord.Member, *, bot_user_id: int | None = None) -> AgentSnapshot:
    raw_status = "online" if member.id == bot_user_id else str(member.status)
    try:
        status = PresenceStatus(raw_status)
    except ValueError:
        status = None
    activities = tuple(
        snapshot
        for activity in member.activities
        if (snapshot := activity_snapshot(activity)) is not None
    )
    return AgentSnapshot(
        key=AgentKey(guild_id=member.guild.id, user_id=member.id),
        display_name=member.display_name,
        status=status,
        is_bot=member.bot,
        activities=activities,
    )


def message_snapshot(message: discord.Message) -> MessageSnapshot | None:
    if message.guild is None:
        return None
    return MessageSnapshot(
        key=AgentKey(guild_id=message.guild.id, user_id=message.author.id),
        message_id=message.id,
        content=message.clean_content or "",
    )


__all__ = ["activity_snapshot", "member_snapshot", "message_snapshot"]
