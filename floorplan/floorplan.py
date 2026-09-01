"""Thin Pixel Index-only Floorplan Cog composition."""

from __future__ import annotations

from redbot.core import commands

from .adapters.admin_commands import AdminCommandsMixin
from .adapters.catalogue_commands import CatalogueCommandsMixin
from .adapters.replies import ReplyMixin

__all__ = ["Floorplan"]


class Floorplan(AdminCommandsMixin, CatalogueCommandsMixin, ReplyMixin, commands.Cog):
    """Configure, browse, and load Pixel Index layouts."""
