"""Red entrypoint with dependency-aware imports for lightweight tooling.

`PixelAgents`/`pixelagents` are resolved lazily, through `__getattr__` below,
never eagerly at this module's own top level. `.pixelagents` (the Cog module)
transitively imports `adapters/replies.py`, which needs `corridor` already
loaded -- fine when reached through `setup()` below (which loads corridor
first), but importing it here unconditionally would make `import pixelagents`
alone -- e.g. `corridor.dependency_loader.ensure_importable`'s whole point is
letting a dependent do exactly that without loading pixelagents as a Cog --
cache `pixelagents.adapters.replies` in `sys.modules` bound to whatever
corridor state happened to hold at that moment. That caching is the trap:
Red's `_load` always calls `_cleanup_and_refresh_modules` before `setup()`,
which unconditionally re-execs every already-cached `pixelagents.*` submodule
-- bypassing this file's own lazy resolution entirely -- so a later
`[p]load pixelagents`, at a moment corridor isn't currently loaded, would
re-run that stale cached module's `from corridor.domain import ReplyField`
and crash with `ModuleNotFoundError` before `setup()` ever gets a chance to
load corridor. Keeping this file corridor-free until `PixelAgents`/`setup()`
is actually requested means nothing ever caches that submodule prematurely.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from redbot.core.bot import Red

    from .pixelagents import PixelAgents as PixelAgents
    from .pixelagents import pixelagents as pixelagents

__all__ = ["pixelagents"]


def _publish_cog(cog_class: type[object]) -> type[object]:
    globals()["PixelAgents"] = cog_class
    globals()["pixelagents"] = cog_class
    return cog_class


_DATA_STATEMENT = "This cog does not persistently store any data or metadata about users."

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

    from .dependency_loader import ensure_corridor_loaded

    await ensure_corridor_loaded(bot)
    from .pixelagents import PixelAgents

    await bot.add_cog(PixelAgents(bot))
    _publish_cog(PixelAgents)
