"""Dependency composition and lifecycle for the Toolbox Cog."""

from __future__ import annotations

from typing import Any

from redbot.core import data_manager
from redbot.core.bot import Red

from ..application import NodeService
from ..dependency_loader import ensure_corridor_loaded
from ..infrastructure import NodeInstaller, RedNodeRepository


class CogBase:
    """Wire services once and own resources spanning the Cog lifetime."""

    bot: Red
    config: Any

    def __init__(self, bot: Red) -> None:
        self.bot = bot
        self._repository = RedNodeRepository.create(self)
        self.config = self._repository.config
        # Not the installed toolbox/ tree: Downloader manages that and never
        # writes anything into it, so installed runtimes live in Red's
        # per-cog data directory instead -- writable, and persists across
        # cog updates/reloads.
        install_root = data_manager.cog_data_path(self) / "node"
        self._installer = NodeInstaller(install_root=install_root)
        self._service = NodeService(self._repository, self._installer)
        self._corridor: Any = None

    async def cog_load(self) -> None:
        """Extension point for start-up work (background tasks, sessions, ...).

        required_cogs in info.json is only a Downloader install hint -- Red
        does not auto-load a dependency at runtime just because it's
        declared there, so ensure_corridor_loaded() pulls corridor back in
        if it was unloaded independently.
        """

        self._corridor = await ensure_corridor_loaded(self.bot)
        # So unloading corridor cascades to unload this cog too, instead of
        # leaving it running with a stale corridor reference.
        self._corridor.register_dependent("toolbox")
        await self._service.reactivate()

    async def cog_unload(self) -> None:
        """Extension point for teardown work."""

        if self._corridor is not None:
            self._corridor.unregister_dependent("toolbox")
