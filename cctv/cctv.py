"""Thin CCTV Cog composition."""

from __future__ import annotations

from redbot.core import commands

from .adapters.commands import CommandsMixin
from .adapters.dashboard import DashboardMixin, dashboard_page
from .adapters.replies import ReplyMixin

__all__ = ["CCTV", "dashboard_page"]


class CCTV(DashboardMixin, CommandsMixin, ReplyMixin, commands.Cog):
    """Serve and project the Discord and editor office pages."""
