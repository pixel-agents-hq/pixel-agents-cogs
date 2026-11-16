"""Thin Pixel Index-only Floorplan Cog composition."""

from __future__ import annotations

from typing import Literal

from redbot.core import commands

from .adapters.admin_commands import AdminCommandsMixin
from .adapters.catalogue_commands import CatalogueCommandsMixin
from .adapters.replies import ReplyMixin

__all__ = ["Floorplan"]


class Floorplan(AdminCommandsMixin, CatalogueCommandsMixin, ReplyMixin, commands.Cog):
    """Configure, browse, and load Pixel Index layouts."""

    async def red_delete_data_for_user(
        self,
        *,
        requester: Literal["discord_deleted_user", "owner", "user", "user_strict"],
        user_id: int,
    ) -> None:
        """Nothing to delete, for every requester type: no user ID or user
        content is stored. Floorplan stores only the bot-wide Pixel Index
        API and web URLs; a layout it loads replaces the editor office's
        layout, which holds no user data (seats are pixelagents' to
        delete)."""

        del requester, user_id
