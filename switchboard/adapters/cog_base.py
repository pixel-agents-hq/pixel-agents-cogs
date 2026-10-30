"""Dependency composition and lifecycle for the Switchboard Cog.

Switchboard deliberately has no corridor dependency (no dependency_loader.py,
empty required_cogs): its job is to install and load corridor, so it must
load in an environment where corridor cannot even import.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import sys
from typing import Any

from redbot.core.bot import Red

from ..application import SwitchboardService
from ..infrastructure import (
    CogCatalog,
    PipInstaller,
    RedSwitchboardSettings,
    build_lookup,
    downloader_lib_path,
    read_downloader_index,
)
from . import rendering
from .extensions import RedExtensions

log = logging.getLogger("red.office-cogs.switchboard")


class CogBase:
    """Wire services once and own resources spanning the Cog lifetime."""

    bot: Red
    config: Any

    def __init__(self, bot: Red) -> None:
        self.bot = bot
        self._settings = RedSwitchboardSettings.create(self)
        self.config = self._settings.config
        self._service = SwitchboardService(
            catalog=CogCatalog(bot._cog_mgr, downloader_index=read_downloader_index),
            installer=PipInstaller(downloader_lib_path),
            lookup_factory=lambda: build_lookup(sys.path),
            extensions=RedExtensions(bot),
            settings=self._settings,
        )
        self._repair_task: asyncio.Task[None] | None = None

    async def cog_load(self) -> None:
        self._register_rpc_handlers()
        if await self._settings.autorepair():
            self._repair_task = asyncio.create_task(self._repair_when_ready())

    async def cog_unload(self) -> None:
        if self._repair_task is not None:
            self._repair_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._repair_task
            self._repair_task = None

    def _register_rpc_handlers(self) -> None:
        """Provided by RpcMixin."""

        raise NotImplementedError

    async def _repair_when_ready(self) -> None:
        """Red drops a cog from its autoload list when it fails at startup (for
        example corridor before its requirements exist), so reload the
        managed set once startup autoloading is over."""

        await self.bot.wait_until_red_ready()
        try:
            report = await self._service.repair()
        except Exception:
            log.exception("Autorepair failed")
            return
        if report is not None and report.order:
            log.info("Autorepair finished:\n%s", rendering.load_text(report))
