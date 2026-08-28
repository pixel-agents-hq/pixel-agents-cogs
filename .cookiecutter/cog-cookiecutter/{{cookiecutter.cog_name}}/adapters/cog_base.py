"""Dependency composition and lifecycle for the {{ cookiecutter.cog_name.replace('-', '_').split('_') | map('capitalize') | join }} Cog."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from redbot.core.bot import Red

from ..application import CounterService
from ..dependency_loader import ensure_corridor_loaded
from ..infrastructure import RedCounterRepository

# Conventional path for this cog's own bundled avatar image -- passed to
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
        self._repository = RedCounterRepository.create(self)
        self.config = self._repository.config
        self._service = CounterService(self._repository)
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
        self._corridor.register_dependent("{{cookiecutter.cog_name}}")
        # Bound once, reused at every reply call site in adapters/commands.py
        # (self._reply.send_reply(...)) instead of repeating this cog's
        # owner name as an argument everywhere -- see
        # docs/reply-identity-design.md. `category` (Agent/Room/Furniture,
        # see docs/embed-colors.md) is left unset here: a freshly generated
        # cog doesn't know which bucket it fits yet, if any -- pass
        # `category=` once that's decided, matching how deskutils/
        # pixelagents deliberately stay uncategorized today.
        self._reply = self._corridor.reply_sender(
            owner="{{ cookiecutter.cog_name.replace('-', '_').split('_') | map('capitalize') | join }}",
            avatar_path=AVATAR_PATH,
        )
        # Scans self for @llm_tool-decorated commands (the bump/project/
        # report examples in adapters/commands.py) and registers each.
        # Inert if pico (or any other LLM-tool consumer) never loads:
        # corridor's registry just holds them unread. See
        # docs/corridor-tool-registry-design.md.
        self._corridor.register_llm_tools(self, owner="{{ cookiecutter.cog_name.replace('-', '_').split('_') | map('capitalize') | join }}")

    async def cog_unload(self) -> None:
        """Extension point for teardown work."""

        if self._corridor is not None:
            self._corridor.unregister_tool_owner("{{ cookiecutter.cog_name.replace('-', '_').split('_') | map('capitalize') | join }}")
            self._corridor.unregister_dependent("{{cookiecutter.cog_name}}")
