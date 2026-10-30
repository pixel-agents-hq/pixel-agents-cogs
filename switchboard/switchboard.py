"""Composition root: assembles the adapter mixins into the Red Cog class."""

from __future__ import annotations

from redbot.core import commands

from .adapters.cog_base import CogBase
from .adapters.commands import CommandsMixin
from .adapters.rpc import RpcMixin


class Switchboard(CommandsMixin, RpcMixin, CogBase, commands.Cog):
    """Install requirements for addpath cogs and load/unload cogs in dependency order."""
