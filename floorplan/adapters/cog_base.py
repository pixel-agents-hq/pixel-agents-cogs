"""Pixel Index-only Floorplan composition and lifecycle."""

from __future__ import annotations

import logging
from typing import Any, cast

import discord
from redbot.core import commands
from redbot.core.bot import Red

from corridor.domain import OfficeStateKind

from ..application import CatalogueService
from ..dependency_loader import ensure_corridor_loaded
from ..infrastructure.pixel_index import PixelIndexClient
from ..infrastructure.settings import RedSettingsRepository

log = logging.getLogger("red.d_cogs.floorplan")


class FloorplanBase:
    bot: Red
    config: Any

    def __init__(self, bot: Red) -> None:
        self.bot = bot
        self._settings_repository = RedSettingsRepository.create(self)
        self.config = self._settings_repository.config
        self._pixel_index_client = PixelIndexClient(logger=log)
        self._corridor: Any = None
        self._pixelagents: Any = None
        self._catalogue_service = CatalogueService(
            self._settings_repository,
            self._pixel_index_client,
            can_edit_layout=self._can_edit_layout_user,
            apply_layout=self._apply_catalogue_layout,
        )

    async def cog_load(self) -> None:
        from corridor.dependency_loader import ensure_loaded

        try:
            self._corridor = await ensure_corridor_loaded(self.bot)
            self._corridor.register_dependent("floorplan")
            self._corridor.register_llm_tools(self, owner="Floorplan")
            self._pixelagents = await ensure_loaded(self.bot, "pixelagents", "PixelAgents")
            await self._pixel_index_client.start()
        except Exception:
            await self.cog_unload()
            raise

    async def cog_unload(self) -> None:
        if self._corridor is not None:
            self._corridor.unregister_tool_owner("Floorplan")
            self._corridor.unregister_dependent("floorplan")
        await self._pixel_index_client.close()

    async def _apply_catalogue_layout(self, layout: dict[str, object]) -> None:
        await self._pixelagents.set_office_layout(OfficeStateKind.DISCORD, layout)

    async def _can_edit_layout_user(self, user_id: int) -> bool:
        if user_id == 0:
            return False
        owner = cast("discord.User", discord.Object(id=user_id))
        if await self.bot.is_owner(owner):
            return True
        for guild in self.bot.guilds:
            member = guild.get_member(user_id)
            if member is not None and await self._corridor.capabilities_satisfy(
                member, "keyholder"
            ):
                return True
        return False

    async def _reply(
        self, ctx: commands.Context, content: str | None = None, **kwargs: Any
    ) -> None:
        raise NotImplementedError

    async def _send_public(
        self, ctx: commands.Context, content: str | None = None, **kwargs: Any
    ) -> None:
        raise NotImplementedError


__all__ = ["FloorplanBase", "log"]
