"""Composition root: assembles the adapter mixins into the Red Cog class."""

from __future__ import annotations

from redbot.core import commands

from .adapters.cog_base import CogBase
from .adapters.commands import CommandsMixin


class Animator(CommandsMixin, CogBase, commands.Cog):
    """A2A pixel-art rendering agent backed by pixel-art-mcp."""
