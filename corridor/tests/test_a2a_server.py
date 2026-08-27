"""A2AServer's bind-probe/uvicorn-lifecycle behavior is a straight
relocation of architect's former test coverage (see
docs/agent-directory-design.md) -- these bind-failure cases stay
identical. The routing behavior is new: real HTTP requests (aiohttp, a
declared corridor dependency) against a real bound listener, verifying
`rebuild_routes` mounts/unmounts an agent's path live -- not mocked,
matching the "verified for real" bar `architect/tests/
test_office_websocket_live.py` already set for the socket this code was
relocated from."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import aiohttp
from a2a.server.agent_execution.agent_executor import AgentExecutor
from a2a.server.agent_execution.context import RequestContext
from a2a.server.events.event_queue import EventQueue
from a2a.types import AgentCapabilities, AgentCard, AgentInterface
from a2a.utils import TransportProtocol

from ..domain import RegisteredAgent
from ..infrastructure.a2a_server import A2AServer


class DummyExecutor(AgentExecutor):
    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        raise NotImplementedError

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        raise NotImplementedError


def _agent(agent_key: str, *, avatar_path: Path | None = None) -> RegisteredAgent:
    card = AgentCard(
        name=agent_key,
        description="A test agent.",
        version="0.1.0",
        supported_interfaces=[
            AgentInterface(
                url=f"http://127.0.0.1:0/{agent_key}/",
                protocol_binding=TransportProtocol.JSONRPC.value,
            )
        ],
        capabilities=AgentCapabilities(),
        default_input_modes=["text/plain"],
        default_output_modes=["text/plain"],
        skills=[],
    )
    return RegisteredAgent(
        agent_key=agent_key, card=card, executor=DummyExecutor(), avatar_path=avatar_path
    )


class TestA2AServerBindFailure(unittest.IsolatedAsyncioTestCase):
    """Regression coverage for the real production incident
    docs/architect-design.md §9 documents, relocated verbatim: uvicorn's
    own Server.startup() calls sys.exit() on a bind failure, which must
    be reported back as an error string, never allowed to raise."""

    async def test_start_reports_a_bind_failure_instead_of_raising(self) -> None:
        first = A2AServer()
        first_error = await first.start(host="127.0.0.1", port=8950)
        self.addAsyncCleanup(first.stop)
        self.assertIsNone(first_error)

        second = A2AServer()

        second_error = await second.start(host="127.0.0.1", port=8950)

        self.assertIsNotNone(second_error)
        self.assertFalse(second.running)

    async def test_a_failed_start_does_not_leave_a_dangling_server_or_task(self) -> None:
        server = A2AServer()
        blocker = A2AServer()
        await blocker.start(host="127.0.0.1", port=8951)
        self.addAsyncCleanup(blocker.stop)

        error = await server.start(host="127.0.0.1", port=8951)

        self.assertIsNotNone(error)
        self.assertFalse(server.running)
        await server.stop()  # must not raise even though start() never fully succeeded


class TestA2AServerRouting(unittest.IsolatedAsyncioTestCase):
    async def test_zero_agents_mounts_nothing(self) -> None:
        server = A2AServer()
        await server.start(host="127.0.0.1", port=8952)
        self.addAsyncCleanup(server.stop)

        async with aiohttp.ClientSession() as session:
            async with session.get(
                "http://127.0.0.1:8952/architect/.well-known/agent-card.json"
            ) as response:
                self.assertEqual(response.status, 404)

    async def test_start_with_agents_mounts_each_under_its_own_path(self) -> None:
        server = A2AServer()
        await server.start(host="127.0.0.1", port=8953, agents=[_agent("architect")])
        self.addAsyncCleanup(server.stop)

        async with aiohttp.ClientSession() as session:
            async with session.get(
                "http://127.0.0.1:8953/architect/.well-known/agent-card.json"
            ) as response:
                self.assertEqual(response.status, 200)
                body = await response.json()
                self.assertEqual(body["name"], "architect")

    async def test_rebuild_routes_mounts_a_newly_registered_agent_live(self) -> None:
        server = A2AServer()
        await server.start(host="127.0.0.1", port=8954)
        self.addAsyncCleanup(server.stop)

        server.rebuild_routes([_agent("architect")])

        async with aiohttp.ClientSession() as session:
            async with session.get(
                "http://127.0.0.1:8954/architect/.well-known/agent-card.json"
            ) as response:
                self.assertEqual(response.status, 200)

    async def test_rebuild_routes_unmounts_a_removed_agent_live(self) -> None:
        server = A2AServer()
        await server.start(host="127.0.0.1", port=8955, agents=[_agent("architect")])
        self.addAsyncCleanup(server.stop)

        server.rebuild_routes([])

        async with aiohttp.ClientSession() as session:
            async with session.get(
                "http://127.0.0.1:8955/architect/.well-known/agent-card.json"
            ) as response:
                self.assertEqual(response.status, 404)

    async def test_rebuild_routes_before_start_is_a_noop(self) -> None:
        server = A2AServer()

        server.rebuild_routes([_agent("architect")])  # must not raise


class TestA2AServerAvatarRoute(unittest.IsolatedAsyncioTestCase):
    """See docs/reply-identity-design.md section 7 -- corridor serves an
    agent's bundled avatar off the same per-agent Mount its A2A routes
    already use, so ConsultAgentTool can show it as a footer identity."""

    async def test_serves_the_avatar_when_the_file_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            avatar_path = Path(tmp) / "avatar.png"
            avatar_path.write_bytes(b"fake-png-bytes")
            server = A2AServer()
            await server.start(
                host="127.0.0.1", port=8956, agents=[_agent("architect", avatar_path=avatar_path)]
            )
            self.addAsyncCleanup(server.stop)

            async with aiohttp.ClientSession() as session:
                async with session.get("http://127.0.0.1:8956/architect/avatar.png") as response:
                    self.assertEqual(response.status, 200)
                    self.assertEqual(await response.read(), b"fake-png-bytes")

    async def test_404s_when_the_configured_avatar_file_is_missing(self) -> None:
        server = A2AServer()
        await server.start(
            host="127.0.0.1",
            port=8957,
            agents=[_agent("architect", avatar_path=Path("/does/not/exist/avatar.png"))],
        )
        self.addAsyncCleanup(server.stop)

        async with aiohttp.ClientSession() as session:
            async with session.get("http://127.0.0.1:8957/architect/avatar.png") as response:
                self.assertEqual(response.status, 404)

    async def test_no_avatar_route_mounted_without_an_avatar_path(self) -> None:
        server = A2AServer()
        await server.start(host="127.0.0.1", port=8958, agents=[_agent("architect")])
        self.addAsyncCleanup(server.stop)

        async with aiohttp.ClientSession() as session:
            async with session.get("http://127.0.0.1:8958/architect/avatar.png") as response:
                self.assertEqual(response.status, 404)


if __name__ == "__main__":
    unittest.main()
