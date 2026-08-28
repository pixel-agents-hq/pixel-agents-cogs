"""The category->color mapping backing docs/embed-colors.md. Plain hex ints,
not discord.Colour -- this package has zero framework imports by design
(see agent_directory.py's docstring for the one deliberate exception). The
adapter layer (`corridor/adapters/api.py`) is the only place that turns one
of these into a real discord.Colour."""

from .models import ReplyCategory

REPLY_CATEGORY_COLORS: dict[ReplyCategory, int] = {
    ReplyCategory.AGENT: 0x5865F2,  # Discord blurple
    ReplyCategory.ROOM: 0x1ABC9C,  # Discord teal
    ReplyCategory.FURNITURE: 0xF1C40F,  # Discord gold
}

__all__ = ["REPLY_CATEGORY_COLORS"]
