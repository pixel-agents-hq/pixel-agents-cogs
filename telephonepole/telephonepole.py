"""Composition root: assembles the adapter mixins into the Red Cog class."""

from __future__ import annotations

from redbot.core import commands

from .adapters.cog_base import CogBase
from .adapters.commands import CommandsMixin


class Telephonepole(CommandsMixin, CogBase, commands.Cog):
    """Dynamically registers third-party MCP servers for registered A2A agents."""
