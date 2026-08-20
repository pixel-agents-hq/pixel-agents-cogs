"""Composition root: assembles the adapter mixins into the Red Cog class."""

from __future__ import annotations

from redbot.core import commands

from .adapters.cog_base import CogBase
from .adapters.commands import CommandsMixin
from .adapters.listener import ListenerMixin


class Pico(CommandsMixin, ListenerMixin, CogBase, commands.Cog):
    """Decide whether to react to a message, and if so, act only via tool calls."""
