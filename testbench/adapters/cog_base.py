"""Dependency composition and lifecycle for the Testbench Cog.

testbench is stateless -- it never persists anything (no per-guild Config),
it only asks corridor to publish an event on demand -- so there is no
repository/service to wire here, unlike the cookiecutter template's
scaffolded CounterService example. Only the corridor connection and its
register_dependent/unregister_dependent lifecycle remain."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from redbot.core.bot import Red

from ..dependency_loader import ensure_corridor_loaded

# Conventional path for testbench's own bundled avatar image -- passed to
# corridor.reply_sender() regardless of whether a real file exists here
# yet; existence is checked fresh on every send, so dropping a real image
# at this exact path later needs no code change. See
# docs/reply-identity-design.md.
AVATAR_PATH = Path(__file__).resolve().parent.parent / "assets" / "avatar.png"


class CogBase:
    """Wire services once and own resources spanning the Cog lifetime."""

    bot: Red

    def __init__(self, bot: Red) -> None:
        self.bot = bot
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
        self._corridor.register_dependent("testbench")
        self._reply = self._corridor.reply_sender(owner="Testbench", avatar_path=AVATAR_PATH)

    async def cog_unload(self) -> None:
        """Extension point for teardown work."""

        if self._corridor is not None:
            self._corridor.unregister_dependent("testbench")
