"""AgentDirectoryService is fully testable without Red: a real a2a-sdk
AgentCard plus a trivial AgentExecutor double stand in for a real
registration, same shape as test_tool_registry_service.py's plain
RegisteredTool values -- no unittest.mock needed."""

from __future__ import annotations

import unittest

from a2a.server.agent_execution.agent_executor import AgentExecutor
from a2a.server.agent_execution.context import RequestContext
from a2a.server.events.event_queue import EventQueue
from a2a.types import AgentCapabilities, AgentCard, AgentInterface
from a2a.utils import TransportProtocol

from ..application import AgentDirectoryService
from ..domain import RegisteredAgent


class DummyExecutor(AgentExecutor):
    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        raise NotImplementedError

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        raise NotImplementedError


def _card(name: str = "agent") -> AgentCard:
    return AgentCard(
        name=name,
        description="A test agent.",
        version="0.1.0",
        supported_interfaces=[
            AgentInterface(
                url="http://127.0.0.1:0/", protocol_binding=TransportProtocol.JSONRPC.value
            )
        ],
        capabilities=AgentCapabilities(),
        default_input_modes=["text/plain"],
        default_output_modes=["text/plain"],
        skills=[],
    )


def _agent(agent_key: str) -> RegisteredAgent:
    return RegisteredAgent(agent_key=agent_key, card=_card(agent_key), executor=DummyExecutor())


class TestAgentDirectoryService(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = AgentDirectoryService()

    def test_list_agents_with_nothing_registered_is_empty(self) -> None:
        self.assertEqual(self.directory.list_agents(), ())

    def test_registered_agent_is_listed(self) -> None:
        agent = _agent("architect")
        self.directory.register(agent, owner="Architect")

        self.assertEqual(self.directory.list_agents(), (agent,))

    def test_multiple_owners_agents_are_all_listed(self) -> None:
        a = _agent("architect")
        b = _agent("agent-n")
        self.directory.register(a, owner="Architect")
        self.directory.register(b, owner="AgentN")

        self.assertEqual(
            {agent.agent_key for agent in self.directory.list_agents()}, {"architect", "agent-n"}
        )

    def test_same_owner_reregistration_overwrites(self) -> None:
        first = _agent("architect")
        second = _agent("architect")
        self.directory.register(first, owner="Architect")

        self.directory.register(second, owner="Architect")

        self.assertEqual(self.directory.list_agents(), (second,))

    def test_different_owner_name_collision_raises(self) -> None:
        self.directory.register(_agent("architect"), owner="Architect")

        with self.assertRaises(ValueError):
            self.directory.register(_agent("architect"), owner="Impostor")

    def test_unregister_owner_drops_only_that_owners_agents(self) -> None:
        a = _agent("architect")
        b = _agent("agent-n")
        self.directory.register(a, owner="Architect")
        self.directory.register(b, owner="AgentN")

        self.directory.unregister_owner("Architect")

        self.assertEqual(self.directory.list_agents(), (b,))

    def test_unregister_owner_for_unknown_owner_is_a_noop(self) -> None:
        self.directory.unregister_owner("nobody")  # must not raise

    def test_unregister_by_key_removes_regardless_of_owner(self) -> None:
        self.directory.register(_agent("architect"), owner="Architect")

        self.directory.unregister("architect")

        self.assertEqual(self.directory.list_agents(), ())

    def test_list_agents_for_owner_returns_only_that_owners_agents(self) -> None:
        a = _agent("architect")
        b = _agent("agent-n")
        self.directory.register(a, owner="Architect")
        self.directory.register(b, owner="AgentN")

        self.assertEqual(self.directory.list_agents_for_owner("Architect"), (a,))

    def test_list_agents_for_owner_with_nothing_registered_is_empty(self) -> None:
        self.assertEqual(self.directory.list_agents_for_owner("nobody"), ())


if __name__ == "__main__":
    unittest.main()
