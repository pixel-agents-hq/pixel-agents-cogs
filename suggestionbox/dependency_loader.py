"""Load runtime cog dependencies before importing dependency-bound adapters."""

from __future__ import annotations

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


def _raise_load_error(message: str, *, cause: Exception | None = None) -> NoReturn:
    """Raise Red's user-facing load error without importing Red during discovery."""

    from redbot.core.errors import CogLoadError

    if cause is None:
        raise CogLoadError(message)
    raise CogLoadError(message) from cause
