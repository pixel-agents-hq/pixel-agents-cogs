"""Composition root: assembles the adapter mixins into the Red Cog class."""

from __future__ import annotations

from typing import Literal

from redbot.core import commands

from .adapters.cog_base import CogBase
from .adapters.commands import CommandsMixin


class Suggestionbox(CommandsMixin, CogBase, commands.Cog):
    """MCP feedback server for reporting errors/improvements, per-agent gated."""

    async def red_delete_data_for_user(
        self,
        *,
        requester: Literal["discord_deleted_user", "owner", "user", "user_strict"],
        user_id: int,
    ) -> None:
        """Nothing to delete, for every requester type: no user ID or user
        content is stored. Suggestionbox stores only the feedback channel,
        its MCP listener address and the per-agent access list. A report is
        posted to the feedback channel as a Discord message and not kept by
        the cog."""

        del requester, user_id
