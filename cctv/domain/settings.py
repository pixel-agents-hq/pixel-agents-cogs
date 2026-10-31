"""Immutable CCTV settings snapshots."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class GlobalSettings:
    listener_host: str
    listener_port: int
    discord_clear_delay: float
    editor_clear_delay: float
    # Pixel Index's character designer, where a member copies a seed code.
    designer_url: str
    # Where browsers reach the Dashboard (`https://host`), so `[p]cctv url`
    # can hand out an absolute link; None until the bot owner sets it.
    public_base_url: str | None


@dataclass(frozen=True, slots=True)
class GuildSettings:
    guild_id: int
    enabled: bool
    include_bots: bool
    # Show members whose Discord status is offline/invisible too.
    show_offline: bool
    # Draw those offline members translucent (only with show_offline).
    ghost_offline: bool
    # Draw bot accounts translucent.
    ghost_bots: bool
    # Show members' Discord rich presence (Spotify, games) on this office.
    broadcast_rich_presence: bool
    # Show members' chat messages, from every channel the bot reads, as
    # speech bubbles on this office.
    broadcast_messages: bool


__all__ = ["GlobalSettings", "GuildSettings"]
