"""Composition root: assembles the adapter mixins into the Red Cog class."""

from __future__ import annotations

from redbot.core import commands

from .adapters.cog_base import CogBase
from .adapters.commands import CommandsMixin


class Bootcamp(CommandsMixin, CogBase, commands.Cog):
    """Dynamically create custom LLM agents with their own system prompt, registered as A2A agents."""
