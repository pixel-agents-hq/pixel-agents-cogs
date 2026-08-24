"""Dependency composition and lifecycle for the Deskutils Cog."""

from __future__ import annotations

from typing import Any

from redbot.core.bot import Red

from ..application import TimeService
from ..dependency_loader import ensure_corridor_loaded
from ..infrastructure import SystemClock


class CogBase:
    """Wire services once and own resources spanning the Cog lifetime.

    No Red Config here: unlike most cogs in this repo, deskutils has
    nothing to persist -- `[p]deskutils time` only ever reads the system
    clock and a caller-supplied zone name, both stateless.
    """

    bot: Red

    def __init__(self, bot: Red) -> None:
        self.bot = bot
        self._service = TimeService(SystemClock())
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
        self._corridor.register_dependent("deskutils")
        # Scans self for @llm_tool-decorated commands (time_command) and
        # registers each -- inert if pico (or any other LLM-tool consumer)
        # never loads, corridor's registry just holds it unread. See
        # docs/corridor-tool-registry-design.md.
        self._corridor.register_llm_tools(self, owner="Deskutils")

    async def cog_unload(self) -> None:
        """Extension point for teardown work."""

        if self._corridor is not None:
            self._corridor.unregister_tool_owner("Deskutils")
            self._corridor.unregister_dependent("deskutils")
