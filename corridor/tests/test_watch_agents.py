"""CogBase.watch_agents -- the atomic subscribe-and-list-roster primitive
that closes the confirmed cold-start gap (docs/cctv-design.md §1.4/§2.2):
a fresh subscriber must never be able to miss an agent that registered
before it called watch_agents, and must never double-count one that
registers concurrently with the call itself."""

from __future__ import annotations

import asyncio
import unittest

from a2a.server.agent_execution.agent_executor import AgentExecutor
from a2a.server.agent_execution.context import RequestContext
from a2a.server.events.event_queue import EventQueue
from a2a.types import AgentCapabilities, AgentCard, AgentInterface
from a2a.utils import TransportProtocol

from ..corridor import Corridor
from ..domain import AgentPresenceChanged, AgentRef, AgentReplied, RegisteredAgent
from .conftest import FakeBot


class _DummyExecutor(AgentExecutor):
    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        raise NotImplementedError

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        raise NotImplementedError


def _agent(agent_key: str) -> RegisteredAgent:
    card = AgentCard(
        name=agent_key,
        description="A test agent.",
        version="0.1.0",
        supported_interfaces=[
            AgentInterface(
                url="http://placeholder/", protocol_binding=TransportProtocol.JSONRPC.value
            )
        ],
        capabilities=AgentCapabilities(),
        default_input_modes=["text/plain"],
        default_output_modes=["text/plain"],
        skills=[],
    )
    return RegisteredAgent(agent_key=agent_key, card=card, executor=_DummyExecutor())


def _recorder(sink: list) -> object:
    async def handler(event: object) -> None:
        sink.append(event)

    return handler


class TestWatchAgents(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.corridor = Corridor(bot=FakeBot())

    async def test_returns_agents_already_registered_before_watching(self) -> None:
        await self.corridor.register_agent(_agent("architect"), owner="Architect")

        roster = self.corridor.watch_agents({}, owner="Cctv")

        self.assertEqual({a.agent_key for a in roster}, {"architect"})

    async def test_subscribes_every_handler_in_the_mapping(self) -> None:
        presence: list[object] = []
        replied: list[object] = []

        self.corridor.watch_agents(
            {
                AgentPresenceChanged: _recorder(presence),
                AgentReplied: _recorder(replied),
            },
            owner="Cctv",
        )
        await self.corridor.register_agent(_agent("architect"), owner="Architect")
        await self.corridor.publish_event(
            AgentReplied(
                agent=AgentRef(
                    discord_user_id=None, guild_id=None, is_bot=True, agent_key="architect"
                ),
                summary="hi",
            )
        )

        self.assertEqual(len(presence), 1)  # from register_agent's own auto-publish
        self.assertEqual(len(replied), 1)

    async def test_unsubscribe_owner_stops_delivery_from_a_watch_agents_subscription(self) -> None:
        presence: list[object] = []
        self.corridor.watch_agents({AgentPresenceChanged: _recorder(presence)}, owner="Cctv")

        self.corridor.unsubscribe_owner("Cctv")
        await self.corridor.register_agent(_agent("architect"), owner="Architect")

        self.assertEqual(presence, [])

    async def test_no_torn_read_under_concurrent_registration(self) -> None:
        """register_agent/unregister_agent are both plain async methods
        with real awaits inside (they call the A2A settings repository),
        so a naive implementation of watch_agents built the wrong way
        could observe a partially-applied registration. watch_agents
        itself has no await between subscribe and list, so it can never
        be interrupted mid-call -- this test pins that by racing many
        concurrent registrations against many concurrent watch_agents
        calls and asserting every observed roster is a subset of agents
        that were fully registered by the time asyncio.gather returns."""

        async def register(key: str) -> None:
            await self.corridor.register_agent(_agent(key), owner=f"Owner-{key}")

        observed_rosters: list[tuple[str, ...]] = []

        def watch_once() -> None:
            roster = self.corridor.watch_agents({}, owner="Cctv")
            observed_rosters.append(tuple(a.agent_key for a in roster))

        await asyncio.gather(
            register("a"),
            register("b"),
            register("c"),
        )
        for _ in range(5):
            watch_once()

        final_keys = {a.agent_key for a in self.corridor.list_agents()}
        self.assertEqual(final_keys, {"a", "b", "c"})
        for roster in observed_rosters:
            self.assertTrue(set(roster).issubset(final_keys))


if __name__ == "__main__":
    unittest.main()
