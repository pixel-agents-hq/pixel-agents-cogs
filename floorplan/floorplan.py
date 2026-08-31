"""Thin Floorplan Cog composition.

floorplan no longer hosts any dashboard/WebSocket surface -- see
docs/cctv-design.md. What's left is Pixel Index browsing and loading a
catalogue layout into the shared Discord-page office layout.
"""

from __future__ import annotations

from redbot.core import commands

from pixelagents.application.office import DEFAULT_PALETTE_COUNT, JS_MAX_SAFE, to_agent_id

from .adapters.admin_commands import AdminCommandsMixin
from .adapters.catalogue_commands import CatalogueCommandsMixin
from .adapters.layout_views import LayoutBrowseView, LayoutDetailView, absolute_url
from .adapters.replies import ReplyMixin
from .application import LAYOUT_SORT_CHOICES

__all__ = ["Floorplan"]

# Stable names retained for downstream imports, same convention pixelagents
# used before the split.
_LAYOUT_SORT_CHOICES = LAYOUT_SORT_CHOICES
_LayoutBrowseView = LayoutBrowseView
_LayoutDetailView = LayoutDetailView
_abs_url = absolute_url
_JS_MAX_SAFE = JS_MAX_SAFE
_PALETTE_COUNT = DEFAULT_PALETTE_COUNT


def _discord_id_to_agent_id(user_id: int) -> int:
    """Compatibility wrapper for the domain ID mapping."""

    return to_agent_id(user_id)


class Floorplan(
    ReplyMixin,
    AdminCommandsMixin,
    CatalogueCommandsMixin,
    commands.Cog,
):
    """Browse and load shared office layouts from Pixel Index."""
