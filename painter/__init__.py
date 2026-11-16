"""Red entrypoint with a dependency-aware import.

The straightforward `from .painter import ...` would force
Red/discord.py to be importable the moment this package is imported at all --
including by pytest's conftest.py discovery, which must import this
`__init__.py` before it can reach `conftest.py` in this same package. Defer
the real import so the package stays importable (and tests' stub modules get
a chance to install) even without Red/discord.py present.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from redbot.core.bot import Red

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
    # Corridor must be loaded before importing this Cog: its adapters import
    # Corridor's public domain API at module scope.
    from .dependency_loader import ensure_corridor_loaded

    await ensure_corridor_loaded(bot)
    # pixelagents only needs to be *importable*, not a fully loaded Cog, at
    # this point: adapters/cog_base.py imports Pixelagents' Semantic IR codec
    # and furniture style loader at module scope -- the same convention
    # architect's own __init__.py follows for the same reason.
    from corridor.dependency_loader import ensure_importable

    await ensure_importable(bot, "pixelagents")
    from .painter import Painter

    await bot.add_cog(Painter(bot))
