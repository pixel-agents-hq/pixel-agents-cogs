"""Composition root: assembles the adapter mixins into the Red Cog class."""

from __future__ import annotations

from typing import Literal

from redbot.core import commands

from .adapters.cog_base import CogBase
from .adapters.commands import CommandsMixin


class Painter(CommandsMixin, CogBase, commands.Cog):
    """Recolors architect's office layout -- tiles, walls, and furniture -- without touching its structure."""

    async def red_delete_data_for_user(
        self,
        *,
        requester: Literal["discord_deleted_user", "owner", "user", "user_strict"],
        user_id: int,
    ) -> None:
        """Nothing to delete, for every requester type: no user ID or user
        content is stored. Painter stores only bot-wide prompt, tool-budget
        and logging settings. A2A task text is sent to the LLM endpoint for
        the one task and never kept; the colors it edits hold no user
        data."""

        del requester, user_id
