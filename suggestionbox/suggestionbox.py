"""Composition root: assembles the adapter mixins into the Red Cog class."""

from __future__ import annotations

from redbot.core import commands

from .adapters.cog_base import CogBase
from .adapters.commands import CommandsMixin


class Suggestionbox(CommandsMixin, CogBase, commands.Cog):
    """MCP feedback server for reporting errors/improvements, per-agent gated."""
