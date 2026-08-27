"""ConsultAgentTool: a FakeArchitectAsker double for the handler's
`ArchitectClient.ask` call -- what the real A2A round trip does is covered
by architect's own test suite plus manual verification against a live
loopback listener (see docs/architect-design.md), not duplicated here."""

from __future__ import annotations

import unittest

from ..infrastructure.architect_client import ArchitectRequestError
from ..tools.consult_agent_tool import ConsultAgentInput, ConsultAgentOutput, ConsultAgentTool


class FakeArchitectAsker:
    def __init__(self, *, answer: str | None = None, fail_with: Exception | None = None) -> None:
        self.answer = answer
        self.fail_with = fail_with
        self.calls: list[dict[str, str]] = []

    async def ask(self, *, base_url: str, text: str) -> str:
        self.calls.append({"base_url": base_url, "text": text})
        if self.fail_with is not None:
            raise self.fail_with
        assert self.answer is not None
        return self.answer


def _tool(client: FakeArchitectAsker, *, agent_key: str = "architect") -> ConsultAgentTool:
    return ConsultAgentTool(
        client,
        agent_key=agent_key,
        base_url="http://localhost:8931/architect/",
        description="A test agent.",
    )


class TestConsultAgentTool(unittest.IsolatedAsyncioTestCase):
    async def test_handler_returns_the_answer_on_success(self) -> None:
        client = FakeArchitectAsker(answer="the answer")
        tool = _tool(client)

        output = await tool.handler(ConsultAgentInput(prompt="what is the answer?"))

        assert isinstance(output, ConsultAgentOutput)
        self.assertEqual(output.status, "ok")
        self.assertEqual(output.answer, "the answer")
        self.assertIsNone(output.error)
        self.assertEqual(
            client.calls,
            [{"base_url": "http://localhost:8931/architect/", "text": "what is the answer?"}],
        )

    async def test_handler_reports_an_error_without_raising(self) -> None:
        client = FakeArchitectAsker(fail_with=ArchitectRequestError("architect is unreachable"))
        tool = _tool(client)

        output = await tool.handler(ConsultAgentInput(prompt="anything"))

        assert isinstance(output, ConsultAgentOutput)
        self.assertEqual(output.status, "error")
        self.assertIsNone(output.answer)
        self.assertEqual(output.error, "architect is unreachable")

    def test_input_schema_has_a_required_prompt(self) -> None:
        schema = _tool(FakeArchitectAsker()).Input.model_json_schema()

        self.assertIn("prompt", schema["properties"])
        self.assertIn("prompt", schema["required"])

    def test_name_is_derived_from_the_agent_key(self) -> None:
        tool = _tool(FakeArchitectAsker(), agent_key="agent-n")

        self.assertEqual(tool.name, "consult_agent-n")

    def test_description_comes_from_the_agent_card(self) -> None:
        tool = _tool(FakeArchitectAsker())

        self.assertEqual(tool.description, "A test agent.")

    def test_falls_back_to_a_generic_description_when_the_card_has_none(self) -> None:
        tool = ConsultAgentTool(
            FakeArchitectAsker(),
            agent_key="architect",
            base_url="http://localhost:8931/architect/",
            description="",
        )

        self.assertEqual(tool.description, "Delegate a task to architect.")


if __name__ == "__main__":
    unittest.main()
