"""ConsultAgentTool: a FakeArchitectAsker double for the handler's
`ArchitectClient.ask` call -- what the real A2A round trip does is covered
by architect's own test suite plus manual verification against a live
loopback listener (see docs/architect-design.md), not duplicated here.

Also covers the tool's own Discord announcements (the outgoing question,
then the raw answer or a failure) -- a deliberate transparency feature, see
this tool's own module docstring."""

from __future__ import annotations

import unittest
from typing import Any

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


class FakeCorridor:
    def __init__(self) -> None:
        self.replies: list[str | None] = []

    async def send_reply(self, ctx: object, *, description: str | None = None, **_: Any) -> None:
        self.replies.append(description)


def _tool(
    client: FakeArchitectAsker, corridor: FakeCorridor, *, agent_key: str = "architect"
) -> ConsultAgentTool:
    return ConsultAgentTool(
        client,
        corridor,
        object(),
        agent_key=agent_key,
        base_url="http://localhost:8931/architect/",
        description="A test agent.",
    )


class TestConsultAgentTool(unittest.IsolatedAsyncioTestCase):
    async def test_handler_returns_the_answer_on_success(self) -> None:
        client = FakeArchitectAsker(answer="the answer")
        tool = _tool(client, FakeCorridor())

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
        tool = _tool(client, FakeCorridor())

        output = await tool.handler(ConsultAgentInput(prompt="anything"))

        assert isinstance(output, ConsultAgentOutput)
        self.assertEqual(output.status, "error")
        self.assertIsNone(output.answer)
        self.assertEqual(output.error, "architect is unreachable")

    def test_input_schema_has_a_required_prompt(self) -> None:
        schema = _tool(FakeArchitectAsker(), FakeCorridor()).Input.model_json_schema()

        self.assertIn("prompt", schema["properties"])
        self.assertIn("prompt", schema["required"])

    def test_name_is_derived_from_the_agent_key(self) -> None:
        tool = _tool(FakeArchitectAsker(), FakeCorridor(), agent_key="agent-n")

        self.assertEqual(tool.name, "consult_agent-n")

    def test_description_comes_from_the_agent_card(self) -> None:
        tool = _tool(FakeArchitectAsker(), FakeCorridor())

        self.assertEqual(tool.description, "A test agent.")

    def test_falls_back_to_a_generic_description_when_the_card_has_none(self) -> None:
        tool = ConsultAgentTool(
            FakeArchitectAsker(),
            FakeCorridor(),
            object(),
            agent_key="architect",
            base_url="http://localhost:8931/architect/",
            description="",
        )

        self.assertEqual(tool.description, "Delegate a task to architect.")


class TestConsultAgentToolAnnouncements(unittest.IsolatedAsyncioTestCase):
    """The A2A exchange itself is posted to Discord deterministically --
    independent of whatever pico's own LLM later says via ReplyTool."""

    async def test_announces_the_question_then_the_raw_answer(self) -> None:
        corridor = FakeCorridor()
        tool = _tool(FakeArchitectAsker(answer="38 items total"), corridor)

        await tool.handler(ConsultAgentInput(prompt="what furniture exists?"))

        self.assertEqual(len(corridor.replies), 2)
        self.assertIn("what furniture exists?", corridor.replies[0] or "")
        self.assertIn("architect", corridor.replies[0] or "")
        self.assertIn("38 items total", corridor.replies[1] or "")
        self.assertIn("architect", corridor.replies[1] or "")

    async def test_announces_the_question_then_a_failure(self) -> None:
        corridor = FakeCorridor()
        tool = _tool(FakeArchitectAsker(fail_with=ArchitectRequestError("unreachable")), corridor)

        await tool.handler(ConsultAgentInput(prompt="anything"))

        self.assertEqual(len(corridor.replies), 2)
        self.assertIn("anything", corridor.replies[0] or "")
        self.assertIn("unreachable", corridor.replies[1] or "")

    async def test_a_broken_announcement_does_not_fail_the_tool_call(self) -> None:
        class BrokenCorridor(FakeCorridor):
            async def send_reply(self, ctx: object, **kwargs: Any) -> None:
                raise RuntimeError("channel gone")

        tool = _tool(FakeArchitectAsker(answer="fine"), BrokenCorridor())

        output = await tool.handler(ConsultAgentInput(prompt="anything"))

        assert isinstance(output, ConsultAgentOutput)
        self.assertEqual(output.status, "ok")
        self.assertEqual(output.answer, "fine")


if __name__ == "__main__":
    unittest.main()
