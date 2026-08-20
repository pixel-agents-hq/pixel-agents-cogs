"""Dependency composition and lifecycle for the Pico Cog."""

from __future__ import annotations

from typing import Any

from redbot.core.bot import Red

from ..application import GateService, ToolLoopService
from ..dependency_loader import ensure_corridor_loaded
from ..infrastructure import LiteLLMClient, RedPicoRepository


class CogBase:
    """Wire services once and own resources spanning the Cog lifetime."""

    bot: Red
    config: Any

    def __init__(self, bot: Red) -> None:
        self.bot = bot
        self._repository = RedPicoRepository.create(self)
        self.config = self._repository.config
        self._llm_client = LiteLLMClient()
        self._gate_service = GateService(self._llm_client)
        self._tool_loop_service = ToolLoopService(self._llm_client)
        self._corridor: Any = None

    async def cog_load(self) -> None:
        """required_cogs in info.json is only a Downloader install hint --
        Red does not auto-load a dependency at runtime just because it's
        declared there, so ensure_corridor_loaded() pulls corridor back in
        if it was unloaded independently."""

        self._corridor = await ensure_corridor_loaded(self.bot)
        # So unloading corridor cascades to unload this cog too, instead of
        # leaving it running with a stale corridor reference.
        self._corridor.register_dependent("pico")
        # No eager `_llm_client.start()` here -- most messages/commands
        # never touch the LLM at all (see the gate's rule-based fast path),
        # so the session opens lazily on first actual use instead.

    async def cog_unload(self) -> None:
        if self._corridor is not None:
            self._corridor.unregister_dependent("pico")
        await self._llm_client.close()
