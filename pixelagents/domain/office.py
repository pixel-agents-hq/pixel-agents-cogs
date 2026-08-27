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
class GenuineAgentKey:
    """Identity of a genuine agent -- one with no Discord account, e.g.
    architect. Parallel to AgentKey, never a variant of it: AgentKey's
    fields are real Discord snowflakes by construction, and a genuine
    agent doesn't have one to supply. `agent_key` is a short, stable slug
    ("architect") -- see corridor.domain.AgentRef.agent_key, the field
    this is built from. See docs/office-agent-identity-design.md."""

    agent_key: str


# The identity shape every OfficeService entry point that used to take
# only AgentKey now accepts -- is_tracked, highlight_agent,
# unhighlight_agent, start_tool_activity, set_status, send_message_activity,
# clear_message_activity.
OfficeIdentity: TypeAlias = AgentKey | GenuineAgentKey


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
