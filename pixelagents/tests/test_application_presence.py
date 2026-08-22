"""Focused tests for PresenceService."""

from __future__ import annotations

import unittest

from pixelagents.application import PresenceService
from pixelagents.domain import (
    ActivityKind,
    ActivitySnapshot,
    AgentKey,
    AgentSnapshot,
    MessageSnapshot,
    PresenceStatus,
)


def agent(
    *,
    guild_id: int = 1,
    user_id: int = 2,
    name: str = "Agent",
    status: PresenceStatus | None = PresenceStatus.ONLINE,
    activities: tuple[ActivitySnapshot, ...] = (),
) -> AgentSnapshot:
    return AgentSnapshot(
        key=AgentKey(guild_id, user_id),
        display_name=name,
        status=status,
        is_bot=False,
        activities=activities,
    )


class TestPresenceService(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.sent: list[dict[str, object]] = []

        async def send(message) -> None:
            self.sent.append(dict(message))

        self.service = PresenceService(send)

    async def test_listening_activity_is_preferred_and_cached(self) -> None:
        snapshot = agent(
            activities=(
                ActivitySnapshot(kind=ActivityKind.PLAYING, name="Game"),
                ActivitySnapshot(
                    kind=ActivityKind.LISTENING,
                    name="Spotify",
                    title="Track",
                    artist="Artist",
                ),
            )
        )

        await self.service.start(snapshot, -2, enabled=True)
        await self.service.update(snapshot, -2, enabled=True)

        self.assertEqual(self.service.cache[(1, 2)], "Track — Artist")
        self.assertEqual([message["type"] for message in self.sent], ["agentToolStart"])

    async def test_changed_label_clears_before_replacement(self) -> None:
        first = agent(activities=(ActivitySnapshot(kind=ActivityKind.PLAYING, name="One"),))
        second = agent(activities=(ActivitySnapshot(kind=ActivityKind.PLAYING, name="Two"),))

        await self.service.start(first, -2, enabled=True)
        self.sent.clear()
        await self.service.update(second, -2, enabled=True)

        self.assertEqual(
            [message["type"] for message in self.sent],
            ["agentToolsClear", "agentToolStart"],
        )

    def test_message_projection_truncates_at_the_locked_boundary(self) -> None:
        snapshot = MessageSnapshot(AgentKey(1, 2), message_id=99, content="x" * 100)
        projected = self.service.message_start(snapshot, -2)

        self.assertEqual(projected["toolId"], "msg-99")
        self.assertEqual(projected["status"], "x" * 40 + "…")
