"""Immutable domain snapshots with no framework or transport dependencies.

Consuming cogs normalize their own gateway objects (Discord members, messages,
or any future source) into these values before handing them to the
`application` services. Keeping the data immutable prevents a cached gateway
object or mutable config result from changing underneath a long-running
reconciliation operation.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TypeAlias

SnowflakeId: TypeAlias = int
AgentId: TypeAlias = int


class PresenceStatus(StrEnum):
    """Presence states that produce a visible office agent."""

    ONLINE = "online"
    IDLE = "idle"
    DO_NOT_DISTURB = "dnd"


class ActivityKind(StrEnum):
    """Normalized activity categories."""

    PLAYING = "playing"
    STREAMING = "streaming"
    LISTENING = "listening"
    WATCHING = "watching"
    CUSTOM = "custom"
    COMPETING = "competing"


@dataclass(frozen=True, slots=True)
class AgentKey:
    """Identity of a member within one namespace (e.g. a Discord guild)."""

    guild_id: SnowflakeId
    user_id: SnowflakeId


@dataclass(frozen=True, slots=True)
class ActivitySnapshot:
    """The activity fields currently needed to build an office label."""

    kind: ActivityKind
    name: str | None = None
    details: str | None = None
    state: str | None = None
    title: str | None = None
    artist: str | None = None


@dataclass(frozen=True, slots=True)
class AgentSnapshot:
    """A member normalized at the consuming cog's listener boundary."""

    key: AgentKey
    display_name: str
    status: PresenceStatus | None
    is_bot: bool
    activities: tuple[ActivitySnapshot, ...] = ()


@dataclass(frozen=True, slots=True)
class MessageSnapshot:
    """The message fields used by the office activity projection."""

    key: AgentKey
    message_id: SnowflakeId
    content: str
