"""Thin Pixel Agents Cog composition and backwards-compatible exports."""

from __future__ import annotations

from typing import Literal

from redbot.core import commands

from .adapters.cog_base import PixelAgentsBase, WebviewBundleStatus
from .adapters.commands import CommandsMixin
from .adapters.replies import ReplyMixin

__all__ = ["PixelAgents", "WebviewBundleStatus", "pixelagents"]


class PixelAgents(CommandsMixin, ReplyMixin, PixelAgentsBase, commands.Cog):
    """Own the Pixel Agents bundle and validated office-state facade."""

    async def red_delete_data_for_user(
        self,
        *,
        requester: Literal["discord_deleted_user", "owner", "user", "user_strict"],
        user_id: int,
    ) -> None:
        """Every requester type deletes everything: an account's character,
        avatar and seats are cosmetic, none of it is needed to operate or to
        prevent abuse. The account's character is dealt afresh (and stored
        again) only if it shows up in an office again."""

        del requester
        await self.forget_user_data(user_id)


# Red historically loaded and exported this lowercase class name.
pixelagents = PixelAgents
