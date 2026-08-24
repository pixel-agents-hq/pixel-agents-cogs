"""Pure business models. Zero framework imports -- this module never imports
discord.py or redbot, so it is trivially unit-testable without either
installed."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class CurrentTime:
    """A single instant, carried as one tz-aware UTC datetime so every other
    representation -- Discord's `<t:...>` markup, an explicit named zone --
    derives from this one unambiguous source instead of re-reading the
    clock."""

    utc: datetime

    @property
    def epoch_seconds(self) -> int:
        """Whole seconds since epoch: the unit Discord's timestamp markup
        takes."""

        return int(self.utc.timestamp())


@dataclass(frozen=True, slots=True)
class TextStatistics:
    """Character and whitespace-delimited word counts for one string."""

    characters: int
    words: int
