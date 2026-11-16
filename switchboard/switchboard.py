"""Composition root: assembles the adapter mixins into the Red Cog class."""

from __future__ import annotations

from typing import Literal

from redbot.core import commands

from .adapters.cog_base import CogBase
from .adapters.commands import CommandsMixin
from .adapters.rpc import RpcMixin


class Switchboard(CommandsMixin, RpcMixin, CogBase, commands.Cog):
    """Install requirements for addpath cogs and load/unload cogs in dependency order."""

    async def red_delete_data_for_user(
        self,
        *,
        requester: Literal["discord_deleted_user", "owner", "user", "user_strict"],
        user_id: int,
    ) -> None:
        """Nothing to delete, for every requester type: no user ID or user
        content is stored. Switchboard stores only the cogs it manages and
        its autorepair flag."""

        del requester, user_id
