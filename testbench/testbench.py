"""Composition root: assembles the adapter mixins into the Red Cog class."""

from __future__ import annotations

from typing import Literal

from redbot.core import commands

from .adapters.cog_base import CogBase
from .adapters.commands import CommandsMixin


class Testbench(CommandsMixin, CogBase, commands.Cog):
    """Publish corridor bus events manually, for testing."""

    async def red_delete_data_for_user(
        self,
        *,
        requester: Literal["discord_deleted_user", "owner", "user", "user_strict"],
        user_id: int,
    ) -> None:
        """Nothing to delete, for every requester type: no user ID or user
        content is stored. Testbench stores nothing: the events it publishes
        exist only for the moment they are dispatched."""

        del requester, user_id
