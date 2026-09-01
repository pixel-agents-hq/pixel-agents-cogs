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
    "This cog does not persistently store any data or metadata about users. "
    "It stores only bot-wide Pixel Index API and web endpoint URLs."
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
    """Load dependencies without pre-loading Pixelagents as a Cog.

    Floorplan's module imports require Corridor immediately. Pixelagents is
    made importable here and fully loaded from ``Floorplan.cog_load`` when the
    office-state facade is needed.
    """

    from .dependency_loader import ensure_corridor_loaded

    await ensure_corridor_loaded(bot)
    from corridor.dependency_loader import ensure_importable

    await ensure_importable(bot, "pixelagents")
    from .floorplan import Floorplan

    await bot.add_cog(Floorplan(bot))
    globals()["Floorplan"] = Floorplan
