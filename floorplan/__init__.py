"""Red entrypoint with dependency-aware imports for lightweight tooling."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from redbot.core.bot import Red

    from .floorplan import Floorplan as Floorplan
else:
    try:
        from .floorplan import Floorplan as Floorplan
    except ImportError:
        # Contract tools intentionally import this package without Red or
        # discord.py installed; the public name loads on first access instead.
        pass

__all__ = ["Floorplan"]


_DATA_STATEMENT = (
    "This cog transmits Discord user IDs, guild IDs, display names, presence status, and "
    "short message activity snippets to browsers connected to the Pixel Agents office. "
    "It stores one shared office layout and seat assignments globally."
)

try:
    from redbot.core.utils import get_end_user_data_statement_or_raise
except ImportError:
    __red_end_user_data_statement__ = _DATA_STATEMENT
else:
    __red_end_user_data_statement__ = get_end_user_data_statement_or_raise(__file__)


def __getattr__(name: str) -> object:
    if name != "Floorplan":
        raise AttributeError(name)
    from .floorplan import Floorplan

    globals()["Floorplan"] = Floorplan
    return Floorplan


async def setup(bot: Red) -> None:
    """Load the canonical Cog class through Red's standard extension hook."""

    from .dependency_loader import ensure_corridor_loaded, ensure_pixelagents_loaded

    await ensure_corridor_loaded(bot)
    await ensure_pixelagents_loaded(bot)
    from .floorplan import Floorplan

    await bot.add_cog(Floorplan(bot))
    globals()["Floorplan"] = Floorplan
