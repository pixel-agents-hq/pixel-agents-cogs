"""Composition root: assembles the adapter mixins into the Red Cog class."""

from __future__ import annotations

from typing import Literal

from redbot.core import commands

from .adapters.cog_base import CogBase
from .adapters.commands import CommandsMixin


class Toolbox(CommandsMixin, CogBase, commands.Cog):
    """Install and manage development tooling (Node.js/npm) on the bot host."""

    async def red_delete_data_for_user(
        self,
        *,
        requester: Literal["discord_deleted_user", "owner", "user", "user_strict"],
        user_id: int,
    ) -> None:
        """Nothing to delete, for every requester type: no user ID or user
        content is stored. Toolbox stores only the host Node.js install and
        bot-wide and per-server tool toggles, none of it about a user."""

        del requester, user_id
