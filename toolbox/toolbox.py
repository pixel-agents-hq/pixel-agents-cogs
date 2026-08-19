"""Composition root: assembles the adapter mixins into the Red Cog class."""

from __future__ import annotations

from redbot.core import commands

from .adapters.cog_base import CogBase
from .adapters.commands import CommandsMixin


class Toolbox(CommandsMixin, CogBase, commands.Cog):
    """Install and manage development tooling (Node.js/npm) on the bot host."""
