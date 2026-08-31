"""Dependency composition and lifecycle for the Floorplan Cog.

floorplan no longer hosts any dashboard/WebSocket surface -- that moved
to `cctv` (docs/cctv-design.md). What's left is Pixel Index browsing and
loading a catalogue layout into the shared Discord-page office layout,
via pixelagents' `OfficeStateFacade` (never a private Config store of
floorplan's own).
"""

from __future__ import annotations

import logging
from typing import Any

import discord
from redbot.core import commands
from redbot.core.bot import Red

from ..application import CatalogueService
from ..contracts.layout import RawOfficeLayout
from ..dependency_loader import ensure_corridor_loaded
from ..infrastructure.pixel_index import PixelIndexClient
from ..infrastructure.settings import RedSettingsRepository

log = logging.getLogger("red.d_cogs.floorplan")


class PixelAgentsBase:
    """Wire services once and own resources spanning the Cog lifetime --
    named for the runtime it composes, not the Cog class."""

    bot: Red
    config: Any

    def __init__(self, bot: Red) -> None:
        self.bot = bot
        self._corridor: Any = None
        self._pixelagents: Any = None
        self._settings_repository = RedSettingsRepository.create(self)
        self.config = self._settings_repository.config
        self._pixel_index_client = PixelIndexClient(logger=log)
        self._catalogue_service = CatalogueService(
            self._settings_repository,
            self._pixel_index_client,
            can_edit_layout=self._can_edit_layout_user,
            set_layout=self._set_discord_layout,
        )

    async def _set_discord_layout(self, layout: RawOfficeLayout) -> None:
        """The one write path a Pixel Index catalogue load uses -- through
        pixelagents' facade, never a private floorplan Config key. Live
        delivery to any connected `cctv` dashboard page happens
        automatically, via corridor's own `OfficeStateChanged` publish on
        this write; floorplan needs no broadcast of its own.

        Resolves the live `pixelagents` Cog lazily, on this first actual
        use, rather than eagerly in `cog_load()`: floorplan is tested
        *before* pixelagents in the alphabetical Downloader smoke-test
        order, so an eager `ensure_loaded` in `cog_load()` would leave
        pixelagents registered as already-loaded by the time the harness
        gets to testing it independently -- see `setup()`'s own docstring
        in `floorplan/__init__.py` and
        `test_setup_never_touches_pixelagents_either`."""

        from corridor.dependency_loader import ensure_loaded

        self._pixelagents = await ensure_loaded(self.bot, "pixelagents", "PixelAgents")
        await self._pixelagents.office_state().set_discord_layout(layout)

    async def _can_edit_layout_user(self, user_id: int) -> bool:
        """Bot owner, or a `keyholder`-capability member of any guild the
        bot shares with this user -- the same authorization floorplan's
        former in-browser editor used, minus the "guild enabled" gate
        (that concept is now `cctv`'s own, about presence mirroring, not
        about who may load a catalogue layout)."""

        if user_id == 0:
            return False
        owner_candidate = discord.Object(id=user_id)
        if await self.bot.is_owner(owner_candidate):
            return True
        for guild in self.bot.guilds:
            member = guild.get_member(user_id)
            if member is None:
                continue
            if await self._corridor.capabilities_satisfy(member, "keyholder"):
                return True
        return False

    async def cog_load(self) -> None:
        # Red does not call cog_unload() after a failed cog_load(), so
        # clean up here.
        try:
            self._corridor = await ensure_corridor_loaded(self.bot)
            self._corridor.register_dependent("floorplan")
            self._corridor.register_llm_tools(self, owner="Floorplan")
            await self._pixel_index_client.start()
        except Exception:
            await self.cog_unload()
            raise

    async def cog_unload(self) -> None:
        if self._corridor is not None:
            self._corridor.unregister_tool_owner("Floorplan")
            self._corridor.unregister_dependent("floorplan")
        await self._pixel_index_client.close()

    async def _reply(
        self, ctx: commands.Context, content: str | None = None, **kwargs: Any
    ) -> None:
        raise NotImplementedError

    async def _send_public(
        self, ctx: commands.Context, content: str | None = None, **kwargs: Any
    ) -> None:
        raise NotImplementedError
