"""ArchitectTool: a FakeArchitectAsker double for the handler's
`ArchitectClient.ask` call -- what the real A2A round trip does is covered
by architect's own test suite plus manual verification against a live
loopback listener (see docs/architect-design.md), not duplicated here."""

from __future__ import annotations

import unittest

from ..infrastructure.architect_client import ArchitectRequestError
from ..tools.architect_tool import ArchitectTool, ConsultArchitectInput, ConsultArchitectOutput


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


class TestArchitectTool(unittest.IsolatedAsyncioTestCase):
    async def test_handler_returns_the_answer_on_success(self) -> None:
        client = FakeArchitectAsker(answer="the answer")
        tool = ArchitectTool(client, base_url="http://localhost:8931/")

        output = await tool.handler(ConsultArchitectInput(prompt="what is the answer?"))

        assert isinstance(output, ConsultArchitectOutput)
        self.assertEqual(output.status, "ok")
        self.assertEqual(output.answer, "the answer")
        self.assertIsNone(output.error)
        self.assertEqual(
            client.calls, [{"base_url": "http://localhost:8931/", "text": "what is the answer?"}]
        )

    async def test_handler_reports_an_error_without_raising(self) -> None:
        client = FakeArchitectAsker(fail_with=ArchitectRequestError("architect is unreachable"))
        tool = ArchitectTool(client, base_url="http://localhost:8931/")

        output = await tool.handler(ConsultArchitectInput(prompt="anything"))

        assert isinstance(output, ConsultArchitectOutput)
        self.assertEqual(output.status, "error")
        self.assertIsNone(output.answer)
        self.assertEqual(output.error, "architect is unreachable")

    def test_input_schema_has_a_required_prompt(self) -> None:
        schema = ArchitectTool(FakeArchitectAsker(), base_url="http://x/").Input.model_json_schema()

        self.assertIn("prompt", schema["properties"])
        self.assertIn("prompt", schema["required"])
