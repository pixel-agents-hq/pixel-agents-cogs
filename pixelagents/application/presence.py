"""Framework-neutral rich-presence and message activity projection policy."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import Literal, TypeAlias

from ..contracts.outbound import agent_tool_start, agent_tools_clear
from ..domain import ActivityKind, ActivitySnapshot, AgentKey, AgentSnapshot, MessageSnapshot

ServerMessage: TypeAlias = Mapping[str, object]
SendMessage: TypeAlias = Callable[[ServerMessage], Awaitable[None]]


class PresenceService:
    """Build labels and keep the last emitted rich presence per agent."""

    def __init__(self, send: SendMessage) -> None:
        self._send = send
        self._cache: dict[tuple[int, int], str] = {}

    @property
    def cache(self) -> dict[tuple[int, int], str]:
        """Expose the transition cache to legacy adapters during the refactor."""

        return self._cache

    def replace_cache(self, cache: dict[tuple[int, int], str]) -> None:
        self._cache = cache

    @staticmethod
    def pick_activity(snapshot: AgentSnapshot) -> ActivitySnapshot | None:
        activities = [
            activity for activity in snapshot.activities if activity.kind is not ActivityKind.CUSTOM
        ]
        return next(
            (activity for activity in activities if activity.kind is ActivityKind.LISTENING),
            activities[0] if activities else None,
        )

    @classmethod
    def label(cls, snapshot: AgentSnapshot) -> str | None:
        activity = cls.pick_activity(snapshot)
        if activity is None:
            return None
        if activity.kind is ActivityKind.LISTENING:
            if activity.title and activity.artist:
                return f"{activity.title} — {activity.artist}"
            if activity.details and activity.state:
                return f"{activity.details} — {activity.state}"
        return activity.name or None

    @staticmethod
    def agent_status(snapshot: AgentSnapshot) -> Literal["active", "waiting"]:
        return (
            "active"
            if any(activity.kind is not ActivityKind.CUSTOM for activity in snapshot.activities)
            else "waiting"
        )

    @staticmethod
    def message_start(snapshot: MessageSnapshot, agent_id: int) -> ServerMessage:
        content = snapshot.content
        if len(content) > 40:
            content = content[:40] + "…"
        return agent_tool_start(agent_id, f"msg-{snapshot.message_id}", "Message", content)

    async def start(self, snapshot: AgentSnapshot, agent_id: int, *, enabled: bool) -> None:
        if not enabled:
            return
        label = self.label(snapshot)
        if label:
            self._cache[(snapshot.key.guild_id, snapshot.key.user_id)] = label
            await self.send_tool(agent_id, label)

    async def update(self, snapshot: AgentSnapshot, agent_id: int, *, enabled: bool) -> None:
        if not enabled:
            return
        key = (snapshot.key.guild_id, snapshot.key.user_id)
        label = self.label(snapshot)
        cached = self._cache.get(key)
        if label == cached:
            return
        if label:
            self._cache[key] = label
            if cached is not None:
                await self._send(agent_tools_clear(agent_id))
            await self.send_tool(agent_id, label)
            return
        self._cache.pop(key, None)
        await self._send(agent_tools_clear(agent_id))

    async def clear_all(self, agent_ids: tuple[int, ...]) -> None:
        self._cache.clear()
        for agent_id in agent_ids:
            await self._send(agent_tools_clear(agent_id))

    def forget(self, key: AgentKey) -> None:
        self._cache.pop((key.guild_id, key.user_id), None)

    def cached_label(self, key: AgentKey) -> str | None:
        return self._cache.get((key.guild_id, key.user_id))

    def replay(self, active_keys: set[tuple[int, int]]) -> tuple[tuple[AgentKey, str], ...]:
        return tuple(
            (AgentKey(guild_id=guild_id, user_id=user_id), label)
            for (guild_id, user_id), label in self._cache.items()
            if (guild_id, user_id) in active_keys
        )

    async def send_tool(self, agent_id: int, label: str) -> None:
        await self._send(agent_tool_start(agent_id, f"rp-{agent_id}", "Activity", label))
