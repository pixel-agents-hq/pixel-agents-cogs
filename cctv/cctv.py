"""Composition root: assembles the adapter mixins into the Red Cog class.

MRO note: `DiscordGatewayMixin`, `EventSubscriptionsDiscordMixin`, and
`EventSubscriptionsEditorMixin` each override `cog_load`/`cog_unload` and
call `super()` first -- listing them before `CogBase` (which does the
actual corridor/pixelagents/webview/listener wiring) means `CogBase`'s
own work runs first as the `super()` chain unwinds, then each mixin's
own post-wiring registration runs on top of it, the same cooperative
shape floorplan's/architect's own composition roots already use.
"""

from __future__ import annotations

from redbot.core import commands

from .adapters.cog_base import CogBase
from .adapters.commands import CommandsMixin
from .adapters.dashboard import DashboardMixin
from .adapters.discord_gateway import DiscordGatewayMixin
from .adapters.event_subscriptions_discord import EventSubscriptionsDiscordMixin
from .adapters.event_subscriptions_editor import EventSubscriptionsEditorMixin
from .adapters.office_gateway_discord import OfficeGatewayDiscordMixin
from .adapters.office_gateway_editor import OfficeGatewayEditorMixin


class Cctv(
    CommandsMixin,
    DashboardMixin,
    OfficeGatewayDiscordMixin,
    OfficeGatewayEditorMixin,
    DiscordGatewayMixin,
    EventSubscriptionsDiscordMixin,
    EventSubscriptionsEditorMixin,
    CogBase,
    commands.Cog,
):
    """Host the unified Pixel Agents dashboard."""
