"""Framework-independent business data used by Floorplan services.

Agent-visualization types (AgentKey, AgentSnapshot, PresenceStatus, etc.)
moved to `pixelagents.domain` -- import them from there directly.
"""

from .models import GlobalSettings, GuildSettings, SettingsSnapshot, SnowflakeId
from .settings import normalize_http_url

__all__ = [
    "GlobalSettings",
    "GuildSettings",
    "SettingsSnapshot",
    "SnowflakeId",
    "normalize_http_url",
]
