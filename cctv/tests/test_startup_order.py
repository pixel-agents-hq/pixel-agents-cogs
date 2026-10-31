"""Cold-start roster coverage for agents before and after CCTV."""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from corridor.domain import AgentPresenceChanged, AgentRef

from ..adapters.cog_base import CctvBase
from ..application import GuildOffices


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
        self.cctv._offices = GuildOffices()  # type: ignore[attr-defined]
        self.cctv._offices.add(10, self.discord)  # type: ignore[attr-defined,arg-type]
        self.cctv._editor = self.editor  # type: ignore[attr-defined,assignment]
        self.cctv._genuine_agents = {}  # type: ignore[attr-defined]

    async def test_architect_and_painter_registered_before_cctv_seed_both_pages(self) -> None:
        registered = tuple(
            SimpleNamespace(
                agent_key=key,
                card=SimpleNamespace(name=name),
            )
            for key, name in (("architect", "Architect"), ("painter", "Painter"))
        )

        self.cctv._remember_registered_agents(registered)  # type: ignore[arg-type]
        await self.cctv._seed_genuine_agents(self.discord)  # type: ignore[arg-type]
        await self.cctv._seed_genuine_agents(self.editor)  # type: ignore[arg-type]

        expected = [
            ("architect", "Architect", "online"),
            ("painter", "Painter", "online"),
        ]
        self.assertEqual(self.discord.genuine, expected)
        self.assertEqual(self.editor.genuine, expected)

    async def test_an_office_opened_later_seats_the_agents_already_registered(self) -> None:
        await self.cctv._on_agent_presence_changed(  # type: ignore[attr-defined]
            AgentPresenceChanged(
                agent=AgentRef(None, None, True, agent_key="architect"),
                display_name="Architect",
                status="online",
            )
        )
        await self.cctv._on_agent_presence_changed(  # type: ignore[attr-defined]
            AgentPresenceChanged(
                agent=AgentRef(None, None, True, agent_key="painter"),
                display_name="Painter",
                status="offline",
            )
        )
        late = _Pipeline()

        await self.cctv._seed_genuine_agents(late)  # type: ignore[arg-type]

        self.assertEqual(late.genuine, [("architect", "Architect", "online")])

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
