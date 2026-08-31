"""Thin Pixel Agents Cog composition and backwards-compatible exports."""

from __future__ import annotations

from redbot.core import commands

from .adapters.cog_base import PixelAgentsBase, WebviewBundleStatus
from .adapters.commands import CommandsMixin
from .adapters.replies import ReplyMixin

__all__ = ["PixelAgents", "WebviewBundleStatus", "pixelagents"]


class PixelAgents(CommandsMixin, ReplyMixin, PixelAgentsBase, commands.Cog):
    """Own the Pixel Agents bundle and validated office-state facade."""


# Red historically loaded and exported this lowercase class name.
pixelagents = PixelAgents
