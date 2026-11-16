"""Composition root: assembles the adapter mixins into the Red Cog class."""

from __future__ import annotations

from typing import Literal

from redbot.core import commands

from .adapters.cog_base import CogBase
from .adapters.commands import CommandsMixin
from .adapters.discord_gateway import DiscordGatewayMixin


class Corridor(DiscordGatewayMixin, CommandsMixin, CogBase, commands.Cog):
    """Shared reply style and permission tiers for office-cogs."""

    async def red_delete_data_for_user(
        self,
        *,
        requester: Literal["discord_deleted_user", "owner", "user", "user_strict"],
        user_id: int,
    ) -> None:
        """Nothing to delete here, for every requester type.

        Corridor's own settings are bot-wide and per-guild configuration
        (role IDs, never user IDs). The office-state store does hold
        user-linked records -- each account's character and its seats --
        but corridor keeps them as opaque JSON for pixelagents, the one
        layer that knows which keys belong to a user; pixelagents'
        `red_delete_data_for_user` erases them through corridor's
        office-state API, so they are deleted once, not here as well.
        """

        del requester, user_id
