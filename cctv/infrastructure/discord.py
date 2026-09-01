"""Translate discord.py members into Pixel Agents snapshots."""

from __future__ import annotations

from typing import Any, cast

import discord

from pixelagents.domain import (
    ActivityKind,
    ActivitySnapshot,
    AgentKey,
    AgentSnapshot,
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


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def member_snapshot(member: discord.Member, *, bot_user_id: int | None) -> AgentSnapshot:
    raw_status = "online" if member.id == bot_user_id else str(member.status)
    try:
        status = PresenceStatus(raw_status)
    except ValueError:
        status = None
    activities = []
    for activity in member.activities:
        kind = _ACTIVITY_KINDS.get(cast(discord.ActivityType, getattr(activity, "type", None)))
        if kind is not None:
            activities.append(
                ActivitySnapshot(
                    kind=kind,
                    name=_text(getattr(activity, "name", None)),
                    title=_text(getattr(activity, "title", None)),
                    artist=_text(getattr(activity, "artist", None)),
                    details=_text(getattr(activity, "details", None)),
                    state=_text(getattr(activity, "state", None)),
                )
            )
    return AgentSnapshot(
        key=AgentKey(member.guild.id, member.id),
        display_name=member.display_name,
        status=status,
        is_bot=member.bot,
        activities=tuple(activities),
    )


__all__ = ["member_snapshot"]
