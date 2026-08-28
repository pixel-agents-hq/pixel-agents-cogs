"""Dependency composition and lifecycle for the Deskutils Cog."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from redbot.core.bot import Red

from corridor.domain import ReplyCategory

from ..application import TextService, TimeService
from ..dependency_loader import ensure_corridor_loaded
from ..infrastructure import SystemClock

# Conventional path for deskutils' own bundled avatar image -- passed to
# corridor.reply_sender() regardless of whether a real file exists here
# yet; existence is checked fresh on every send, so dropping a real image
# at this exact path later needs no code change. See
# docs/reply-identity-design.md.
AVATAR_PATH = Path(__file__).resolve().parent.parent / "assets" / "avatar.png"


class CogBase:
    """Wire services once and own resources spanning the Cog lifetime.

    No Red Config here: unlike most cogs in this repo, deskutils has
    nothing to persist -- its commands only read the system clock,
    caller-supplied text, or Discord messages already visible to the caller.
    """

    bot: Red

    def __init__(self, bot: Red) -> None:
        self.bot = bot
        self._service = TimeService(SystemClock())
        self._text_service = TextService()
        self._corridor: Any = None
        self._reply: Any = None

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
        self._reply = self._corridor.reply_sender(
            owner="Deskutils", avatar_path=AVATAR_PATH, category=ReplyCategory.FURNITURE
        )
        # Scans self for @llm_tool-decorated commands and
        # registers each -- inert if pico (or any other LLM-tool consumer)
        # never loads, corridor's registry just holds it unread. See
        # docs/corridor-tool-registry-design.md.
        self._corridor.register_llm_tools(self, owner="Deskutils")

    async def cog_unload(self) -> None:
        """Extension point for teardown work."""

        if self._corridor is not None:
            self._corridor.unregister_tool_owner("Deskutils")
            self._corridor.unregister_dependent("deskutils")
