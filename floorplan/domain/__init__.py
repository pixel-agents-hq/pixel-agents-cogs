"""Framework-independent business data used by Floorplan services."""

from .models import (
    ActivityKind,
    ActivitySnapshot,
    AgentId,
    AgentKey,
    AgentSnapshot,
    GlobalSettings,
    GuildSettings,
    MessageSnapshot,
    PresenceStatus,
    SeatAssignment,
    SettingsSnapshot,
    SnowflakeId,
    TrackedAgent,
)
from .settings import normalize_http_url

__all__ = [
    "ActivityKind",
    "ActivitySnapshot",
    "AgentId",
    "AgentKey",
    "AgentSnapshot",
    "GlobalSettings",
    "GuildSettings",
    "MessageSnapshot",
    "PresenceStatus",
    "SeatAssignment",
    "SettingsSnapshot",
    "SnowflakeId",
    "TrackedAgent",
    "normalize_http_url",
]
