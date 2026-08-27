"""Framework-independent business data owned by the Pixel Agents package."""

from .office import (
    ActivityKind,
    ActivitySnapshot,
    AgentId,
    AgentKey,
    AgentSnapshot,
    GenuineAgentKey,
    MessageSnapshot,
    OfficeIdentity,
    PresenceStatus,
    SnowflakeId,
)
from .settings import parse_commit_ref

__all__ = [
    "ActivityKind",
    "ActivitySnapshot",
    "AgentId",
    "AgentKey",
    "AgentSnapshot",
    "GenuineAgentKey",
    "MessageSnapshot",
    "OfficeIdentity",
    "PresenceStatus",
    "SnowflakeId",
    "parse_commit_ref",
]
