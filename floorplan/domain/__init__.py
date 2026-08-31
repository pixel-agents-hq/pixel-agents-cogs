"""Framework-independent business data used by Floorplan services.

Agent-visualization types (AgentKey, AgentSnapshot, PresenceStatus, etc.)
moved to `pixelagents.domain` -- import them from there directly.
"""

from .models import SnowflakeId
from .settings import normalize_http_url

__all__ = [
    "SnowflakeId",
    "normalize_http_url",
]
