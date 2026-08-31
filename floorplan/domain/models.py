"""Immutable domain snapshots with no framework or transport dependencies.

Agent-visualization data (AgentKey, AgentSnapshot, PresenceStatus, etc.)
lives in `pixelagents.domain`. Presence/layout/seats/WebSocket settings
(the former GlobalSettings/GuildSettings/SettingsSnapshot here) moved to
`cctv` along with the dashboard they configured -- floorplan's own
Config identity is down to two URL strings, which need no dataclass
wrapper of their own (see infrastructure/settings.py).
"""

from __future__ import annotations

from typing import TypeAlias

SnowflakeId: TypeAlias = int
