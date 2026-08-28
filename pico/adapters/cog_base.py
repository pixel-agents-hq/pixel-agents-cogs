"""Dependency composition and lifecycle for the Pico Cog."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from redbot.core.bot import Red

from corridor.domain import ReplyCategory

from ..application import GateService, ToolLoopService
from ..dependency_loader import ensure_corridor_loaded
from ..infrastructure import ArchitectClient, CorridorLLMClient, RedPicoRepository

# Conventional path for pico's own bundled avatar image -- passed to
# corridor.reply_sender() regardless of whether a real file exists here
# yet; existence is checked fresh on every send, so dropping a real image
# at this exact path later needs no code change. See
# docs/reply-identity-design.md.
AVATAR_PATH = Path(__file__).resolve().parent.parent / "assets" / "avatar.png"


class CogBase:
    """Wire services once and own resources spanning the Cog lifetime."""

    bot: Red
    config: Any

    def __init__(self, bot: Red) -> None:
        self.bot = bot
        self._repository = RedPicoRepository.create(self)
        self.config = self._repository.config
        self._corridor: Any = None
        self._reply: Any = None
        # The shared LLM connection now lives in corridor (see
        # docs/architect-design.md) -- this proxy defers the actual lookup
        # until each call, since corridor isn't resolved until cog_load().
        llm = CorridorLLMClient(lambda: self._corridor)
        self._gate_service = GateService(llm)
        self._tool_loop_service = ToolLoopService(llm)
        self._architect_client = ArchitectClient()

    async def cog_load(self) -> None:
        """required_cogs in info.json is only a Downloader install hint --
        Red does not auto-load a dependency at runtime just because it's
        declared there, so ensure_corridor_loaded() pulls corridor back in
        if it was unloaded independently."""

        self._corridor = await ensure_corridor_loaded(self.bot)
        # So unloading corridor cascades to unload this cog too, instead of
        # leaving it running with a stale corridor reference.
        self._corridor.register_dependent("pico")
        self._reply = self._corridor.reply_sender(
            owner="Pico", avatar_path=AVATAR_PATH, category=ReplyCategory.AGENT
        )

    async def cog_unload(self) -> None:
        if self._corridor is not None:
            self._corridor.unregister_dependent("pico")
