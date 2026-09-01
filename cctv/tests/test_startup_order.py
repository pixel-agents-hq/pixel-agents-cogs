"""Cold-start roster coverage for agents before and after CCTV."""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from corridor.domain import AgentPresenceChanged, AgentRef, OfficeStateKind

from ..adapters.cog_base import CctvBase


class _Pipeline:
    def __init__(self) -> None:
        self.genuine: list[tuple[str, str, str]] = []

    async def reconcile_genuine(self, identity: object, name: str, status: str) -> None:
        self.genuine.append((identity.agent_key, name, status))  # type: ignore[attr-defined]


class TestAgentColdStartOrder(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.cctv = object.__new__(CctvBase)
        self.discord = _Pipeline()
        self.editor = _Pipeline()
        self.cctv._pipelines = {  # type: ignore[attr-defined]
            OfficeStateKind.DISCORD: self.discord,
            OfficeStateKind.EDITOR: self.editor,
        }

    async def test_architect_and_painter_registered_before_cctv_seed_both_pages(self) -> None:
        registered = tuple(
            SimpleNamespace(
                agent_key=key,
                card=SimpleNamespace(name=name),
            )
            for key, name in (("architect", "Architect"), ("painter", "Painter"))
        )

        await self.cctv._seed_registered_agents(registered)  # type: ignore[arg-type]

        expected = [
            ("architect", "Architect", "online"),
            ("painter", "Painter", "online"),
        ]
        self.assertEqual(self.discord.genuine, expected)
        self.assertEqual(self.editor.genuine, expected)

    async def test_architect_and_painter_registered_after_cctv_reach_both_pages(self) -> None:
        for key, name in (("architect", "Architect"), ("painter", "Painter")):
            await self.cctv._on_agent_presence_changed(
                AgentPresenceChanged(
                    agent=AgentRef(
                        discord_user_id=None,
                        guild_id=None,
                        is_bot=True,
                        agent_key=key,
                    ),
                    display_name=name,
                    status="online",
                )
            )

        expected = [
            ("architect", "Architect", "online"),
            ("painter", "Painter", "online"),
        ]
        self.assertEqual(self.discord.genuine, expected)
        self.assertEqual(self.editor.genuine, expected)


if __name__ == "__main__":
    unittest.main()
