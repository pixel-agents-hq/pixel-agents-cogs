"""Dependency-aware Red entrypoint for CCTV."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from redbot.core.bot import Red

    from .cctv import CCTV as CCTV

__all__ = ["CCTV"]

_DATA_STATEMENT = (
    "This cog stores per-guild display settings. Through corridor's office-state "
    "store, it also persists Discord user IDs and registered agent IDs that have "
    "avatar appearance or seat assignments."
)

try:
    from redbot.core.utils import get_end_user_data_statement_or_raise
except ImportError:
    __red_end_user_data_statement__ = _DATA_STATEMENT
else:
    __red_end_user_data_statement__ = get_end_user_data_statement_or_raise(__file__)


async def setup(bot: Red) -> None:
    from corridor.dependency_loader import ensure_loaded

    await ensure_loaded(bot, "corridor", "Corridor")
    await ensure_loaded(bot, "pixelagents", "PixelAgents")
    from .cctv import CCTV

    await bot.add_cog(CCTV(bot))
