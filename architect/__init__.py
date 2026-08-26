"""Red entrypoint with a dependency-aware import.

The straightforward `from .architect import ...` would force
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

_DATA_STATEMENT = "This cog sends A2A task messages it receives to corridor's configured LLM endpoint. It does not persistently store any data or metadata about Discord users."

try:
    from redbot.core.utils import get_end_user_data_statement_or_raise
except ImportError:
    __red_end_user_data_statement__ = _DATA_STATEMENT
else:
    __red_end_user_data_statement__ = get_end_user_data_statement_or_raise(__file__)


async def setup(bot: Red) -> None:
    # Corridor must be loaded before importing this Cog: its adapters import
    # Corridor's public domain API at module scope.
    from .dependency_loader import ensure_corridor_loaded

    await ensure_corridor_loaded(bot)
    # pixelagents only needs to be *importable*, not a fully loaded Cog, at
    # this point: adapters/cog_base.py imports
    # `pixelagents.application.office.OfficeService` at module scope (same
    # convention floorplan's own setup() follows for the same reason --
    # see floorplan/__init__.py's docstring on ensure_importable vs.
    # ensure_loaded). Becoming a genuine loaded Cog instance stays lazy,
    # resolved on first real use by cog_load() via corridor's ensure_loaded.
    from corridor.dependency_loader import ensure_importable

    await ensure_importable(bot, "pixelagents")
    from .architect import Architect

    await bot.add_cog(Architect(bot))
