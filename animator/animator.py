"""Composition root: assembles the adapter mixins into the Red Cog class."""

from __future__ import annotations

from typing import Literal

from redbot.core import commands

from .adapters.cog_base import CogBase
from .adapters.commands import CommandsMixin


class Animator(CommandsMixin, CogBase, commands.Cog):
    """A2A pixel-art rendering agent backed by pixel-art-mcp."""

    async def red_delete_data_for_user(
        self,
        *,
        requester: Literal["discord_deleted_user", "owner", "user", "user_strict"],
        user_id: int,
    ) -> None:
        """Nothing to delete, for every requester type: no user ID or user
        content is stored. Animator stores only bot-wide prompt,
        tool-budget, logging and timeout settings. A2A task text is sent to
        the LLM endpoint (and tool arguments to pixel-art-mcp) for the one
        task and never kept."""

        del requester, user_id
