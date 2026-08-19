"""Infrastructure adapters for external Pixel Agents dependencies."""

from .client_hub import ClientHub, ClientState
from .discord import activity_snapshot, member_snapshot, message_snapshot
from .pixel_index import PixelIndexClient
from .settings import CONFIG_IDENTIFIER, GLOBAL_DEFAULTS, GUILD_DEFAULTS, RedSettingsRepository
from .tickets import TICKET_TTL_SECONDS, Ticket, TicketStore
from .websocket import WebSocketServer
from .webview import WebviewAssetProvider
from .webview_build import (
    BuildOutcome,
    BuildResult,
    WebviewBuildError,
    build_webview,
    ensure_webview_built,
    missing_tools,
    owner_notification_for,
)

__all__ = [
    "CONFIG_IDENTIFIER",
    "BuildOutcome",
    "BuildResult",
    "ClientHub",
    "ClientState",
    "GLOBAL_DEFAULTS",
    "GUILD_DEFAULTS",
    "PixelIndexClient",
    "RedSettingsRepository",
    "TICKET_TTL_SECONDS",
    "Ticket",
    "TicketStore",
    "WebSocketServer",
    "WebviewAssetProvider",
    "WebviewBuildError",
    "activity_snapshot",
    "build_webview",
    "ensure_webview_built",
    "member_snapshot",
    "message_snapshot",
    "missing_tools",
    "owner_notification_for",
]
