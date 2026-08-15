"""Red entrypoint with dependency-aware imports for lightweight tooling."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from redbot.core.bot import Red

    from .pixelagents import PixelAgents as PixelAgents
    from .pixelagents import pixelagents as pixelagents
else:
    try:
        from .pixelagents import PixelAgents as PixelAgents
        from .pixelagents import pixelagents as pixelagents
    except ImportError:
        # Contract tools intentionally import this package without Red or
        # discord.py installed; the public names load on first access instead.
        pass

__all__ = ["pixelagents"]


def _publish_cog(cog_class: type[object]) -> type[object]:
    globals()["PixelAgents"] = cog_class
    globals()["pixelagents"] = cog_class
    return cog_class


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
    if name not in {"PixelAgents", "pixelagents"}:
        raise AttributeError(name)
    from .pixelagents import PixelAgents

    return _publish_cog(PixelAgents)


async def setup(bot: Red) -> None:
    """Load the canonical Cog class through Red's standard extension hook."""

    from .pixelagents import PixelAgents

    await bot.add_cog(PixelAgents(bot))
    _publish_cog(PixelAgents)
