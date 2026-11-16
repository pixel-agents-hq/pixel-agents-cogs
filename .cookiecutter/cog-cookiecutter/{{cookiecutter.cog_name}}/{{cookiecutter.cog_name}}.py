"""Composition root: assembles the adapter mixins into the Red Cog class."""

from __future__ import annotations

from typing import Literal

from redbot.core import commands

from .adapters.cog_base import CogBase
from .adapters.commands import CommandsMixin


class {{ cookiecutter.cog_name.replace('-', '_').split('_') | map('capitalize') | join }}(CommandsMixin, CogBase, commands.Cog):
    """{{ cookiecutter.short }}"""

    async def red_delete_data_for_user(
        self,
        *,
        requester: Literal["discord_deleted_user", "owner", "user", "user_strict"],
        user_id: int,
    ) -> None:
        """Nothing to delete, for every requester type: the generated cog
        stores no user ID or user content. Once it does, delete it here (or
        in the layer that owns it) and update info.json's
        end_user_data_statement to match."""

        del requester, user_id
