"""Generic runtime cog-dependency loading, reused by any cog that depends on
another cog besides corridor itself.

corridor is the one dependency every cog in this repo bootstraps on its own
(each dependent keeps its own small, unavoidably-duplicated
`ensure_corridor_loaded` -- you cannot import a helper from corridor before
corridor is loaded). Once corridor *is* loaded, though, every other
cross-cog dependency can go through the generic helpers here instead of each
dependent hand-rolling its own `ensure_<name>_loaded`/`ensure_<name>_importable`
pair.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from typing import Any, NoReturn


async def ensure_loaded(bot: Any, package: str, cog_name: str) -> Any:
    """Return the `cog_name` Cog, loading `package` through Red's cog
    manager if needed.

    Fully loads the dependency as a registered Red Cog (`bot.load_extension`
    + `bot.add_loaded_package`). Only call this when a full load as a side
    effect of the caller's own load is safe -- e.g. the dependency is tested
    before the caller by the Downloader RPC smoke test (check-cogs.yml), or
    the caller genuinely needs a live Cog instance right away. If the
    dependency is tested *after* the caller, a full load here leaves it
    registered as already-loaded when the harness later loads it
    independently, and that later test fails -- use `ensure_importable`
    instead in that case (see its docstring for the incident this guards
    against).
    """

    cog = bot.get_cog(cog_name)
    if cog is not None:
        return cog

    # Unloaded third-party cogs are not guaranteed to be importable by name.
    # Red resolves them from its configured cog paths and load_extension then
    # consumes the resulting ModuleSpec.
    try:
        spec = await bot._cog_mgr.find_cog(package)
    except Exception as exc:
        _raise_load_error(f"{cog_name} package discovery failed: {exc}", cause=exc)
    if spec is None:
        _raise_load_error(
            f"{cog_name} is not installed in a configured cog path. Install it before "
            "loading this cog."
        )

    try:
        await bot.load_extension(spec)
    except Exception as exc:
        _raise_load_error(f"{cog_name} could not be auto-loaded: {exc}", cause=exc)

    cog = bot.get_cog(cog_name)
    if cog is None:
        _raise_load_error(f"{cog_name} loaded without registering its Cog.")

    await bot.add_loaded_package(package)
    return cog


async def ensure_importable(bot: Any, package: str) -> None:
    """Make `import <package>` resolvable without loading it as a Cog.

    Some dependents need a dependency's package importable at module scope
    (e.g. `from pixelagents.application.office import OfficeService`)
    immediately when their own module is imported -- before any async
    runtime logic could resolve it lazily. `ensure_loaded` cannot safely be
    used for this when the dependency is tested *after* the caller by the
    Downloader RPC smoke test: a full load there leaves the dependency
    registered as already-loaded when the harness later loads it
    independently, failing that later, supposedly-fresh load.

    Per `redbot.core.bot.Bot.load_extension`, `bot.extensions` (what the
    smoke test's RPC reports as loaded) is only updated *after* the loaded
    module's `setup()` coroutine succeeds -- `spec.loader.load_module()`
    (which populates `sys.modules`, the only part ordinary `import <package>`
    statements need) is a distinct, earlier step. This calls only that step,
    exactly the way Red's own `load_extension` does internally, without ever
    reaching the `setup()`/`bot.extensions` part.
    """

    if package in sys.modules:
        return

    try:
        spec = await bot._cog_mgr.find_cog(package)
    except Exception as exc:
        _raise_load_error(f"{package} package discovery failed: {exc}", cause=exc)
    if spec is None or spec.loader is None:
        _raise_load_error(
            f"{package} is not installed in a configured cog path. Install it before "
            "loading this cog."
        )

    try:
        spec.loader.load_module()
    except Exception as exc:
        _raise_load_error(f"{package} could not be imported: {exc}", cause=exc)


class LazyDependency:
    """Resolve-once, background-eager-loadable handle for a dependency that
    must not be synchronously force-loaded as a side effect of the caller's
    own load, but should still become a genuinely loaded Cog moments after
    the caller loads in normal operation. See docs/dependency-loading.md for
    when to reach for this instead of `ensure_loaded` directly.

    self._pixelagents = LazyDependency(bot, "pixelagents", "PixelAgents")
    # in cog_load(): fire-and-forget, do not await
    self._task_supervisor.create(self._pixelagents.eager_load_in_background())
    # anywhere a live instance is actually needed:
    pixelagents_cog = await self._pixelagents.resolve()
    """

    def __init__(
        self, bot: Any, package: str, cog_name: str, *, logger: logging.Logger | None = None
    ) -> None:
        self._bot = bot
        self._package = package
        self._cog_name = cog_name
        self._cog: Any = None
        self._lock = asyncio.Lock()
        self._log = logger or logging.getLogger(__name__)

    @property
    def value(self) -> Any:
        """The cached Cog, or `None` if `resolve()` hasn't completed yet."""

        return self._cog

    async def resolve(self) -> Any:
        """Fully load the dependency (once), safe against a concurrent caller."""

        if self._cog is None:
            async with self._lock:
                if self._cog is None:
                    self._cog = await ensure_loaded(self._bot, self._package, self._cog_name)
        return self._cog

    async def eager_load_in_background(self) -> None:
        """Best-effort full load shortly after the caller loads. Meant to be
        scheduled via a task supervisor, never awaited directly from the
        caller's own `cog_load()`/`setup()` -- see `resolve`'s docstring and
        docs/dependency-loading.md for why a synchronous load here would be
        unsafe for a dependency tested after the caller by the Downloader
        RPC smoke test."""

        try:
            await self.resolve()
        except Exception:
            self._log.exception(
                "%s: eager load of %s failed; will retry on first use",
                self._cog_name,
                self._package,
            )


def _raise_load_error(message: str, *, cause: Exception | None = None) -> NoReturn:
    """Raise Red's user-facing load error without importing Red during discovery."""

    from redbot.core.errors import CogLoadError

    if cause is None:
        raise CogLoadError(message)
    raise CogLoadError(message) from cause
