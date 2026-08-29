"""Defensive subscription cleanup: if a subscriber's own cog_unload()
crashes partway through without ever reaching its own unsubscribe_owner()
call, corridor must not leak that subscription forever. Red dispatches
on_cog_remove unconditionally after every real cog removal (see
CogBase.on_cog_remove's docstring for why that's a reliable signal), so
this is corridor's own listener for it -- not a replacement for
unsubscribe_owner, an additional safety net for when a subscriber never
gets the chance to call it itself."""

from __future__ import annotations

import unittest
from dataclasses import dataclass
from types import SimpleNamespace

from a2a.server.agent_execution.agent_executor import AgentExecutor
from a2a.server.agent_execution.context import RequestContext
from a2a.server.events.event_queue import EventQueue
from a2a.types import AgentCapabilities, AgentCard, AgentInterface
from a2a.utils import TransportProtocol

from ..corridor import Corridor
from ..domain import AgentPresenceChanged, RegisteredAgent, RegisteredTool
from .conftest import FakeBot


async def _tool_handler(ctx: object, raw_input: object) -> dict[str, object]:
    return {}


def _tool(name: str) -> RegisteredTool:
    return RegisteredTool(
        name=name,
        description="A tool.",
        parameters={"type": "object", "properties": {}},
        handler=_tool_handler,
    )


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


@dataclass(frozen=True, slots=True)
class _Ping:
    value: str


def _recorder(sink: list) -> object:
    async def handler(event: object) -> None:
        sink.append(event)

    return handler


class TestOnCogRemove(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.bot = FakeBot()
        self.corridor = Corridor(bot=self.bot)

    async def test_removed_owners_subscription_stops_receiving_events(self) -> None:
        received: list[object] = []
        self.corridor.subscribe_event(_Ping, _recorder(received), owner="Floorplan")

        await self.corridor.on_cog_remove(SimpleNamespace(qualified_name="Floorplan"))
        await self.corridor.publish_event(_Ping("hi"))

        self.assertEqual(received, [])

    async def test_removal_of_an_unrelated_cog_leaves_other_owners_subscribed(self) -> None:
        received: list[object] = []
        self.corridor.subscribe_event(_Ping, _recorder(received), owner="Floorplan")

        await self.corridor.on_cog_remove(SimpleNamespace(qualified_name="SomeOtherCog"))
        await self.corridor.publish_event(_Ping("hi"))

        self.assertEqual(received, [_Ping("hi")])

    async def test_only_the_removed_owners_subscriptions_are_dropped(self) -> None:
        floorplan_received: list[object] = []
        pico_received: list[object] = []
        self.corridor.subscribe_event(_Ping, _recorder(floorplan_received), owner="Floorplan")
        self.corridor.subscribe_event(_Ping, _recorder(pico_received), owner="Pico")

        await self.corridor.on_cog_remove(SimpleNamespace(qualified_name="Floorplan"))
        await self.corridor.publish_event(_Ping("hi"))

        self.assertEqual(floorplan_received, [])
        self.assertEqual(pico_received, [_Ping("hi")])

    async def test_removed_owners_registered_tools_are_dropped(self) -> None:
        self.corridor.register_tool(_tool("a"), owner="Deskutils")

        await self.corridor.on_cog_remove(SimpleNamespace(qualified_name="Deskutils"))

        self.assertEqual(self.corridor.list_tools(), ())

    async def test_removal_of_an_unrelated_cog_leaves_other_owners_tools_registered(self) -> None:
        self.corridor.register_tool(_tool("a"), owner="Deskutils")

        await self.corridor.on_cog_remove(SimpleNamespace(qualified_name="SomeOtherCog"))

        self.assertEqual({tool.name for tool in self.corridor.list_tools()}, {"a"})

    async def test_removed_owners_registered_agents_are_dropped(self) -> None:
        await self.corridor.register_agent(_agent("architect"), owner="Architect")

        await self.corridor.on_cog_remove(SimpleNamespace(qualified_name="Architect"))

        self.assertEqual(self.corridor.list_agents(), ())

    async def test_removal_of_an_unrelated_cog_leaves_other_owners_agents_registered(self) -> None:
        await self.corridor.register_agent(_agent("architect"), owner="Architect")

        await self.corridor.on_cog_remove(SimpleNamespace(qualified_name="SomeOtherCog"))

        self.assertEqual({agent.agent_key for agent in self.corridor.list_agents()}, {"architect"})

    async def test_removed_owners_agents_get_offline_presence_published(self) -> None:
        received: list[object] = []
        await self.corridor.register_agent(_agent("architect"), owner="Architect")
        self.corridor.subscribe_event(AgentPresenceChanged, _recorder(received), owner="Recorder")

        await self.corridor.on_cog_remove(SimpleNamespace(qualified_name="Architect"))

        self.assertEqual(len(received), 1)
        assert isinstance(received[0], AgentPresenceChanged)
        self.assertEqual(received[0].agent.agent_key, "architect")
        self.assertEqual(received[0].status, "offline")

    async def test_does_not_affect_the_dependent_cascade(self) -> None:
        """This is additive to, not a replacement for, register_dependent's
        own cog_unload cascade -- on_cog_remove only ever touches the
        Pub/Sub bus's subscriber registry."""
        self.corridor.register_dependent("floorplan")

        await self.corridor.on_cog_remove(SimpleNamespace(qualified_name="Floorplan"))

        self.assertIn("floorplan", self.corridor._dependents)


if __name__ == "__main__":
    unittest.main()
