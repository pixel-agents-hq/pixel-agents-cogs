"""Thin Pixel Agents Cog composition and backwards-compatible exports."""

from __future__ import annotations

# `web` is a historical patch point used by integrations and contract tests.
from aiohttp import web
from redbot.core import commands

from .adapters.admin_commands import AdminCommandsMixin
from .adapters.catalogue_commands import CatalogueCommandsMixin
from .adapters.dashboard import DashboardMixin, dashboard_page
from .adapters.discord_gateway import VISIBLE_STATUSES, DiscordGatewayMixin
from .adapters.layout_views import LayoutBrowseView, LayoutDetailView, absolute_url
from .adapters.office_gateway import OfficeGatewayMixin
from .adapters.replies import ReplyMixin
from .adapters.webview_commands import WebviewCommandsMixin
from .application import LAYOUT_SORT_CHOICES
from .application.office import DEFAULT_PALETTE_COUNT, JS_MAX_SAFE, discord_id_to_agent_id

__all__ = ["PixelAgents", "dashboard_page", "pixelagents", "web"]

# Stable compatibility names retained for downstream imports.
_VISIBLE_STATUSES = VISIBLE_STATUSES
_LAYOUT_SORT_CHOICES = LAYOUT_SORT_CHOICES
_LayoutBrowseView = LayoutBrowseView
_LayoutDetailView = LayoutDetailView
_abs_url = absolute_url
_JS_MAX_SAFE = JS_MAX_SAFE
_PALETTE_COUNT = DEFAULT_PALETTE_COUNT


def _discord_id_to_agent_id(user_id: int) -> int:
    """Compatibility wrapper for the domain ID mapping."""

    return discord_id_to_agent_id(user_id)


class PixelAgents(
    DashboardMixin,
    OfficeGatewayMixin,
    DiscordGatewayMixin,
    ReplyMixin,
    AdminCommandsMixin,
    CatalogueCommandsMixin,
    WebviewCommandsMixin,
    commands.Cog,
):
    """Serve the Pixel Agents office and mirror Discord presence into it."""


# Red historically loaded and exported this lowercase class name.
pixelagents = PixelAgents
