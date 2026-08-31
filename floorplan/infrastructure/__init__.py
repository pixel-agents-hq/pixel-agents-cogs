"""Infrastructure adapters for external Floorplan dependencies.

client_hub/discord/tickets/websocket/webview moved to `cctv` along with
the dashboard/WebSocket surface they supported (docs/cctv-design.md) --
what's left is Pixel Index's own HTTP client and floorplan's own
(now minimal) Config settings.
"""

from .pixel_index import PixelIndexClient
from .settings import CONFIG_IDENTIFIER, GLOBAL_DEFAULTS, RedSettingsRepository

__all__ = [
    "CONFIG_IDENTIFIER",
    "GLOBAL_DEFAULTS",
    "PixelIndexClient",
    "RedSettingsRepository",
]
