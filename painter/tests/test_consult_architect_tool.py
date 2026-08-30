"""ConsultArchitectTool: painter's one structural-read tool. Resolves
architect's current A2A URL from a fresh corridor.list_agents() call on
every handler invocation, rather than a fixed base_url -- see the
module's own docstring."""

from __future__ import annotations

import types
import unittest

from ..infrastructure.architect_client import AgentAskResult, ArchitectRequestError
from ..tools.consult_architect_tool import ConsultArchitectInput, ConsultArchitectTool


class FakeArchitectAsker:
    def __init__(self, *, answer: str | None = None, fail_with: Exception | None = None) -> None:
        self._answer = answer
        self._fail_with = fail_with
        self.calls: list[dict[str, object]] = []

    async def ask(self, *, base_url: str, text: str, on_activity: object = None) -> AgentAskResult:
        self.calls.append({"base_url": base_url, "text": text})
        if self._fail_with is not None:
            raise self._fail_with
        assert self._answer is not None
        return AgentAskResult(answer=self._answer)


def _agent(agent_key: str, url: str) -> object:
    return types.SimpleNamespace(
        agent_key=agent_key,
        card=types.SimpleNamespace(supported_interfaces=[types.SimpleNamespace(url=url)]),
    )


class FakeCorridor:
    def __init__(self, agents: tuple[object, ...] = ()) -> None:
        self._agents = agents

    def list_agents(self) -> tuple[object, ...]:
        return self._agents


class TestConsultArchitectTool(unittest.IsolatedAsyncioTestCase):
    async def test_asks_architect_at_its_currently_registered_url(self) -> None:
        client = FakeArchitectAsker(answer="the office is 10x10")
        corridor = FakeCorridor((_agent("architect", "http://arch.example/"),))
        tool = ConsultArchitectTool(client, corridor)  # type: ignore[arg-type]

        output = await tool.handler(ConsultArchitectInput(prompt="how big is the office?"))

        self.assertEqual(output.status, "ok")  # type: ignore[attr-defined]
        self.assertEqual(output.answer, "the office is 10x10")  # type: ignore[attr-defined]
        self.assertEqual(client.calls[0]["base_url"], "http://arch.example/")
        self.assertEqual(client.calls[0]["text"], "how big is the office?")

    async def test_architect_not_registered_reports_an_error_without_calling_the_client(
        self,
    ) -> None:
        client = FakeArchitectAsker(answer="unused")
        corridor = FakeCorridor(())
        tool = ConsultArchitectTool(client, corridor)  # type: ignore[arg-type]

        output = await tool.handler(ConsultArchitectInput(prompt="anything"))

        self.assertEqual(output.status, "error")  # type: ignore[attr-defined]
        assert output.error is not None  # type: ignore[attr-defined]
        self.assertIn("not currently registered", output.error)  # type: ignore[attr-defined]
        self.assertEqual(client.calls, [])

    async def test_a_request_failure_becomes_an_error_output(self) -> None:
        client = FakeArchitectAsker(fail_with=ArchitectRequestError("architect is unreachable"))
        corridor = FakeCorridor((_agent("architect", "http://arch.example/"),))
        tool = ConsultArchitectTool(client, corridor)  # type: ignore[arg-type]

        output = await tool.handler(ConsultArchitectInput(prompt="anything"))

        self.assertEqual(output.status, "error")  # type: ignore[attr-defined]
        self.assertEqual(output.error, "architect is unreachable")  # type: ignore[attr-defined]

    async def test_ignores_other_registered_agents(self) -> None:
        client = FakeArchitectAsker(answer="ok")
        corridor = FakeCorridor(
            (
                _agent("pico", "http://pico.example/"),
                _agent("architect", "http://arch.example/"),
            )
        )
        tool = ConsultArchitectTool(client, corridor)  # type: ignore[arg-type]

        await tool.handler(ConsultArchitectInput(prompt="anything"))

        self.assertEqual(client.calls[0]["base_url"], "http://arch.example/")
