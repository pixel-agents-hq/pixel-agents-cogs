"""Dependency-aware Red entrypoint for CCTV."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from redbot.core.bot import Red

    from .cctv import CCTV as CCTV

__all__ = ["CCTV"]

try:
    from redbot.core.utils import get_end_user_data_statement_or_raise
except ImportError:
    # Without Red (tests, contract tooling): the same info.json key Red reads.
    import json
    from pathlib import Path

    __red_end_user_data_statement__ = json.loads(
        Path(__file__).with_name("info.json").read_text(encoding="utf-8")
    )["end_user_data_statement"]
else:
    __red_end_user_data_statement__ = get_end_user_data_statement_or_raise(__file__)


async def setup(bot: Red) -> None:
    from corridor.dependency_loader import ensure_loaded

    await ensure_loaded(bot, "corridor", "Corridor")
    await ensure_loaded(bot, "pixelagents", "PixelAgents")
    from .cctv import CCTV

    await bot.add_cog(CCTV(bot))
