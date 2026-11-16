"""Composition root: assembles the adapter mixins into the Red Cog class."""

from __future__ import annotations

from typing import Literal

from redbot.core import commands

from .adapters.cog_base import CogBase
from .adapters.commands import CommandsMixin


class Bootcamp(CommandsMixin, CogBase, commands.Cog):
    """Dynamically create custom LLM agents with their own system prompt, registered as A2A agents."""

    async def red_delete_data_for_user(
        self,
        *,
        requester: Literal["discord_deleted_user", "owner", "user", "user_strict"],
        user_id: int,
    ) -> None:
        """Nothing to delete, for every requester type: no user ID or user
        content is stored. Bootcamp stores only the owner-created agents'
        definitions (system prompt, permission group and limits). Prompts
        sent to them go to the LLM endpoint for the one task and are never
        kept."""

        del requester, user_id
