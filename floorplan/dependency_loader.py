"""Load runtime cog dependencies before importing dependency-bound adapters."""

from __future__ import annotations

import sys
from typing import Any, NoReturn


async def ensure_corridor_loaded(bot: Any) -> Any:
    """Return Corridor, loading its package through Red's cog manager if needed."""

    corridor = bot.get_cog("Corridor")
    if corridor is not None:
        return corridor

    # Unloaded third-party cogs are not guaranteed to be importable by name.
    # Red resolves them from its configured cog paths and load_extension then
    # consumes the resulting ModuleSpec.
    try:
        spec = await bot._cog_mgr.find_cog("corridor")
    except Exception as exc:
        _raise_load_error(f"Corridor package discovery failed: {exc}", cause=exc)
    if spec is None:
        _raise_load_error(
            "Corridor is not installed in a configured cog path. Install it before loading "
            "this cog."
        )

    try:
        await bot.load_extension(spec)
    except Exception as exc:
        _raise_load_error(f"Corridor could not be auto-loaded: {exc}", cause=exc)

    corridor = bot.get_cog("Corridor")
    if corridor is None:
        _raise_load_error("Corridor loaded without registering its Cog.")

    await bot.add_loaded_package("corridor")
    return corridor


async def ensure_pixelagents_loaded(bot: Any) -> Any:
    """Return PixelAgents, loading its package through Red's cog manager if needed.

    floorplan depends on pixelagents for the built webview bundle (see
    `PixelAgents.webview_bundle_status()`) -- mirrors `ensure_corridor_loaded`
    above exactly, just against a different dependency.
    """

    pixelagents = bot.get_cog("PixelAgents")
    if pixelagents is not None:
        return pixelagents

    try:
        spec = await bot._cog_mgr.find_cog("pixelagents")
    except Exception as exc:
        _raise_load_error(f"PixelAgents package discovery failed: {exc}", cause=exc)
    if spec is None:
        _raise_load_error(
            "PixelAgents is not installed in a configured cog path. Install it before "
            "loading this cog."
        )

    try:
        await bot.load_extension(spec)
    except Exception as exc:
        _raise_load_error(f"PixelAgents could not be auto-loaded: {exc}", cause=exc)

    pixelagents = bot.get_cog("PixelAgents")
    if pixelagents is None:
        _raise_load_error("PixelAgents loaded without registering its Cog.")

    await bot.add_loaded_package("pixelagents")
    return pixelagents


async def ensure_pixelagents_importable(bot: Any) -> None:
    """Make `import pixelagents` resolvable without loading it as a Cog.

    floorplan's agent-visualization modules import pixelagents' domain/
    application/contracts API at module scope (e.g. `from
    pixelagents.application.office import OfficeService`) -- those statements
    run the instant `.floorplan` is imported in `setup()` below, before any
    async runtime logic could resolve the dependency lazily the way
    `_sync_webview_assets` resolves the real, eventually-needed PixelAgents
    Cog *instance*.

    This deliberately does NOT call `bot.load_extension`/`add_cog` the way
    `ensure_pixelagents_loaded` does. Fully loading pixelagents as a side
    effect of floorplan's own load broke the Downloader RPC smoke test's
    later, independent load of pixelagents (see
    `test_setup_never_touches_pixelagents_either` in
    `floorplan/tests/test_floorplan.py`) -- pixelagents is tested
    alphabetically after floorplan, so anything that leaves it registered as
    already-loaded pollutes that later, supposedly-fresh load.

    Per `redbot.core.bot.Bot.load_extension`, `bot.extensions` (what the
    smoke test's RPC reports as loaded) is only updated *after* the loaded
    module's `setup()` coroutine succeeds -- `spec.loader.load_module()`
    (which populates `sys.modules`, the only part ordinary `import pixelagents`
    statements need) is a distinct, earlier step. This calls only that step,
    exactly the way Red's own `load_extension` does internally, without ever
    reaching the `setup()`/`bot.extensions` part.
    """

    if "pixelagents" in sys.modules:
        return

    try:
        spec = await bot._cog_mgr.find_cog("pixelagents")
    except Exception as exc:
        _raise_load_error(f"PixelAgents package discovery failed: {exc}", cause=exc)
    if spec is None or spec.loader is None:
        _raise_load_error(
            "PixelAgents is not installed in a configured cog path. Install it before "
            "loading this cog."
        )

    try:
        spec.loader.load_module()
    except Exception as exc:
        _raise_load_error(f"PixelAgents could not be imported: {exc}", cause=exc)


def _raise_load_error(message: str, *, cause: Exception | None = None) -> NoReturn:
    """Raise Red's user-facing load error without importing Red during discovery."""

    from redbot.core.errors import CogLoadError

    if cause is None:
        raise CogLoadError(message)
    raise CogLoadError(message) from cause
