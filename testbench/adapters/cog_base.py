"""Dependency composition and lifecycle for the Testbench Cog.

testbench is stateless -- it never persists anything (no per-guild Config),
it only asks corridor to publish an event on demand -- so there is no
repository/service to wire here, unlike the cookiecutter template's
scaffolded CounterService example. Only the corridor connection and its
register_dependent/unregister_dependent lifecycle remain."""

from __future__ import annotations

from typing import Any

from redbot.core.bot import Red

from ..dependency_loader import ensure_corridor_loaded


class CogBase:
    """Wire services once and own resources spanning the Cog lifetime."""

    bot: Red

    def __init__(self, bot: Red) -> None:
        self.bot = bot
        self._corridor: Any = None

    async def cog_load(self) -> None:
        """Extension point for start-up work (background tasks, sessions, ...).

        required_cogs in info.json is only a Downloader install hint -- Red
        does not auto-load a dependency at runtime just because it's
        declared there, so ensure_corridor_loaded() pulls corridor back in if it was
        unloaded independently.
        """

        self._corridor = await ensure_corridor_loaded(self.bot)
        # So unloading corridor cascades to unload this cog too, instead of
        # leaving it running with a stale corridor reference.
        self._corridor.register_dependent("testbench")

    async def cog_unload(self) -> None:
        """Extension point for teardown work."""

        if self._corridor is not None:
            self._corridor.unregister_dependent("testbench")
