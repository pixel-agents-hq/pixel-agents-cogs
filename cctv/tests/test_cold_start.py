"""The exact cold-start gap docs/cctv-design.md §1.4 confirmed and this
refactor was built to close: a genuine A2A agent that registered with
corridor BEFORE cctv's own cog_load runs must still appear on both
pipelines' rosters -- not just agents that register afterward. This is
the property `CogBase.watch_agents` (corridor) plus
`EventSubscriptionsDiscordMixin`/`EventSubscriptionsEditorMixin`'s own
`cog_load` (cctv) exist to guarantee, verified end-to-end here rather
than trusting the unit-level pieces alone."""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from a2a.server.agent_execution.agent_executor import AgentExecutor
from a2a.server.agent_execution.context import RequestContext
from a2a.server.events.event_queue import EventQueue
from a2a.types import AgentCapabilities, AgentCard, AgentInterface
from a2a.utils import TransportProtocol

from pixelagents.domain import GenuineAgentKey

from ..cctv import Cctv
from .conftest import FakeBot, FakeCorridor, FakePixelAgents


class _DummyExecutor(AgentExecutor):
    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        raise NotImplementedError

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        raise NotImplementedError


def _agent(agent_key: str) -> object:
    from corridor.domain import RegisteredAgent

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


class TestColdStartAgentAlreadyRegistered(unittest.IsolatedAsyncioTestCase):
    async def test_an_agent_registered_before_cog_load_still_appears_on_both_rosters(self) -> None:
        corridor = FakeCorridor()
        await corridor.register_agent(_agent("architect"), owner="Architect")

        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        pixelagents = FakePixelAgents(
            corridor=corridor, dist_path=Path(tmp.name), default_layout=None
        )
        bot = FakeBot(corridor=corridor, pixelagents=pixelagents)
        cog = Cctv(bot=bot)

        await cog.cog_load()
        self.addAsyncCleanup(cog.cog_unload)

        # is_tracked(identity) is the same roster-membership check every
        # subscriber handler in both mixins already gates on -- this is
        # the property that actually matters, not a raw ID lookup.
        identity = GenuineAgentKey(agent_key="architect")
        self.assertTrue(cog._discord_office_service.is_tracked(identity))
        self.assertTrue(cog._editor_office_service.is_tracked(identity))


if __name__ == "__main__":
    unittest.main()
