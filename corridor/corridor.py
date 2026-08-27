"""Composition root: assembles the adapter mixins into the Red Cog class."""

from __future__ import annotations

from redbot.core import commands

from .adapters.cog_base import CogBase
from .adapters.commands import CommandsMixin
from .adapters.discord_gateway import DiscordGatewayMixin


class Corridor(DiscordGatewayMixin, CommandsMixin, CogBase, commands.Cog):
    """Shared reply style and permission tiers for office-cogs."""
