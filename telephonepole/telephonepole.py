"""Composition root: assembles the adapter mixins into the Red Cog class."""

from __future__ import annotations

from typing import Literal

from redbot.core import commands

from .adapters.cog_base import CogBase
from .adapters.commands import CommandsMixin


class Telephonepole(CommandsMixin, CogBase, commands.Cog):
    """Dynamically registers third-party MCP servers for registered A2A agents."""

    async def red_delete_data_for_user(
        self,
        *,
        requester: Literal["discord_deleted_user", "owner", "user", "user_strict"],
        user_id: int,
    ) -> None:
        """Nothing to delete, for every requester type: no user ID or user
        content is stored. Telephonepole stores only owner-registered MCP
        server names, URLs and the per-server, per-agent access list."""

        del requester, user_id
