"""Pure business models. Zero framework imports -- this module never imports
discord.py or redbot, so it is trivially unit-testable without either
installed."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CounterSnapshot:
    """An immutable read of one guild's counter value."""

    guild_id: int
    count: int
