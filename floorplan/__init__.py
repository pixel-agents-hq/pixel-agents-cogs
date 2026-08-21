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
    """Load the canonical Cog class through Red's standard extension hook.

    Both corridor and pixelagents are ensured *importable* here: floorplan's
    adapters import each one's public API at module scope (`from
    corridor.domain import ReplyField`, `from pixelagents.application.office
    import OfficeService`), so both packages have to be genuinely resolvable
    before `.floorplan` is imported below.

    corridor is ensured via `ensure_corridor_loaded`, which fully loads it as
    a registered Cog (`bot.load_extension` + `add_cog`) -- safe here because
    the Downloader RPC smoke test (check-cogs.yml) tests corridor first,
    before floorplan, so there is no later independent load of corridor left
    to pollute.

    pixelagents is instead ensured via corridor's generic `ensure_importable`
    (pixelagents is not corridor's own dependent-loading concern -- this just
    reuses the same package now that corridor is guaranteed loaded), which
    stops short of a full Cog load. pixelagents is tested *after* floorplan
    (alphabetically), so fully loading it here as a side effect would leave
    it registered as already-loaded when the harness gets to testing it on
    its own -- see `ensure_importable`'s docstring in
    `corridor/dependency_loader.py` and
    `test_setup_never_touches_pixelagents_either` in
    `floorplan/tests/test_floorplan.py` for the incident this guards against.
    pixelagents becoming a genuinely loaded Cog *instance* (needed for the
    real webview-bundle-status calls) stays lazy, resolved on first actual
    use by `cog_base.py::_sync_webview_assets` via corridor's `ensure_loaded`.
    """

    from .dependency_loader import ensure_corridor_loaded

    await ensure_corridor_loaded(bot)
    from corridor.dependency_loader import ensure_importable

    await ensure_importable(bot, "pixelagents")
    from .floorplan import Floorplan

    await bot.add_cog(Floorplan(bot))
    globals()["Floorplan"] = Floorplan
