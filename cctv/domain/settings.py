"""Immutable CCTV settings snapshots."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class GlobalSettings:
    listener_host: str
    listener_port: int
    discord_clear_delay: float
    editor_clear_delay: float
    broadcast_rich_presence: bool
    broadcast_messages: bool


@dataclass(frozen=True, slots=True)
class GuildSettings:
    guild_id: int
    enabled: bool
    include_bots: bool


__all__ = ["GlobalSettings", "GuildSettings"]
