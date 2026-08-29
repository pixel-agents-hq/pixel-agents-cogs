"""ConsultAgentTool: a FakeArchitectAsker double for the handler's
`ArchitectClient.ask` call -- what the real A2A round trip does is covered
by architect's own test suite plus manual verification against a live
loopback listener (see docs/architect-design.md), not duplicated here.

Also covers the tool's own Discord announcements (the outgoing question,
then the raw answer or a failure) -- a deliberate transparency feature, see
this tool's own module docstring."""

from __future__ import annotations

import unittest
from pathlib import Path
from typing import Any

from ..infrastructure.architect_client import AgentAskResult, ArchitectRequestError
from ..tools.consult_agent_tool import ConsultAgentInput, ConsultAgentOutput, ConsultAgentTool


class FakeArchitectAsker:
    def __init__(
        self,
        *,
        answer: str | None = None,
        tool_calls_made: int | None = None,
        successful_tool_calls: int | None = None,
        failed_tool_calls: int | None = None,
        fail_with: Exception | None = None,
    ) -> None:
        self.answer = answer
        self.tool_calls_made = tool_calls_made
        self.successful_tool_calls = successful_tool_calls
        self.failed_tool_calls = failed_tool_calls
        self.fail_with = fail_with
        self.calls: list[dict[str, str]] = []

    async def ask(self, *, base_url: str, text: str) -> AgentAskResult:
        self.calls.append({"base_url": base_url, "text": text})
        if self.fail_with is not None:
            raise self.fail_with
        assert self.answer is not None
        return AgentAskResult(
            answer=self.answer,
            tool_calls_made=self.tool_calls_made,
            successful_tool_calls=self.successful_tool_calls,
            failed_tool_calls=self.failed_tool_calls,
        )


class FakeReplySender:
    def __init__(self) -> None:
        self.replies: list[str | None] = []
        self.fields: list[Any] = []
        self.footer_overrides: list[Any] = []
        self.footer_icon_paths: list[Path | None] = []

    async def send_reply(
        self,
        ctx: object,
        *,
        description: str | None = None,
        fields: Any = (),
        footer_override: Any = None,
        footer_icon_path: Path | None = None,
        **_: Any,
    ) -> None:
        self.replies.append(description)
        self.fields.append(fields)
        self.footer_overrides.append(footer_override)
        self.footer_icon_paths.append(footer_icon_path)


class FakeCorridor:
    def __init__(self) -> None:
        self.published: list[Any] = []

    async def publish_event(self, event: object) -> None:
        self.published.append(event)


def _tool(
    client: FakeArchitectAsker,
    reply: FakeReplySender,
    *,
    agent_key: str = "architect",
    footer_icon_path: Path | None = None,
    corridor: FakeCorridor | None = None,
    guild_id: int = 1,
    bot_user_id: int | None = 999,
) -> ConsultAgentTool:
    return ConsultAgentTool(
        client,
        reply,
        object(),
        agent_key=agent_key,
        base_url="http://localhost:8931/architect/",
        description="A test agent.",
        corridor=corridor if corridor is not None else FakeCorridor(),
        guild_id=guild_id,
        bot_user_id=bot_user_id,
        footer_icon_path=footer_icon_path,
    )


class TestConsultAgentTool(unittest.IsolatedAsyncioTestCase):
    async def test_handler_returns_the_answer_on_success(self) -> None:
        client = FakeArchitectAsker(answer="the answer")
        tool = _tool(client, FakeReplySender())

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
        tool = _tool(client, FakeReplySender())

        output = await tool.handler(ConsultAgentInput(prompt="anything"))

        assert isinstance(output, ConsultAgentOutput)
        self.assertEqual(output.status, "error")
        self.assertIsNone(output.answer)
        self.assertEqual(output.error, "architect is unreachable")

    def test_input_schema_has_a_required_prompt(self) -> None:
        schema = _tool(FakeArchitectAsker(), FakeReplySender()).Input.model_json_schema()

        self.assertIn("prompt", schema["properties"])
        self.assertIn("prompt", schema["required"])

    def test_name_is_derived_from_the_agent_key(self) -> None:
        tool = _tool(FakeArchitectAsker(), FakeReplySender(), agent_key="agent-n")

        self.assertEqual(tool.name, "consult_agent-n")

    def test_description_comes_from_the_agent_card(self) -> None:
        tool = _tool(FakeArchitectAsker(), FakeReplySender())

        self.assertEqual(tool.description, "A test agent.")

    def test_falls_back_to_a_generic_description_when_the_card_has_none(self) -> None:
        tool = ConsultAgentTool(
            FakeArchitectAsker(),
            FakeReplySender(),
            object(),
            agent_key="architect",
            base_url="http://localhost:8931/architect/",
            description="",
            corridor=FakeCorridor(),
            guild_id=1,
            bot_user_id=999,
        )

        self.assertEqual(tool.description, "Delegate a task to architect.")


class TestConsultAgentToolAnnouncements(unittest.IsolatedAsyncioTestCase):
    """The A2A exchange itself is posted to Discord deterministically --
    independent of whatever pico's own LLM later says via ReplyTool."""

    async def test_announces_the_question_then_the_raw_answer(self) -> None:
        reply = FakeReplySender()
        tool = _tool(FakeArchitectAsker(answer="38 items total"), reply)

        await tool.handler(ConsultAgentInput(prompt="what furniture exists?"))

        self.assertEqual(len(reply.replies), 2)
        self.assertIn("what furniture exists?", reply.replies[0] or "")
        self.assertIn("architect", reply.replies[0] or "")
        self.assertIn("38 items total", reply.replies[1] or "")
        self.assertIn("architect", reply.replies[1] or "")

    async def test_reply_announcement_carries_a_tool_calls_field(self) -> None:
        reply = FakeReplySender()
        tool = _tool(
            FakeArchitectAsker(
                answer="ok", tool_calls_made=3, successful_tool_calls=2, failed_tool_calls=1
            ),
            reply,
        )

        await tool.handler(ConsultAgentInput(prompt="hi"))

        self.assertEqual(reply.fields[0], ())  # the outgoing question carries none
        self.assertEqual(len(reply.fields[1]), 3)
        self.assertEqual(reply.fields[1][0].name, "Tool calls")
        self.assertEqual(reply.fields[1][0].value, "3")
        self.assertEqual(reply.fields[1][1].name, "Successful tool calls")
        self.assertEqual(reply.fields[1][1].value, "2")
        self.assertEqual(reply.fields[1][2].name, "Failing tool calls")
        self.assertEqual(reply.fields[1][2].value, "1")

    async def test_reply_announcement_has_no_tool_call_fields_when_unreported(self) -> None:
        reply = FakeReplySender()
        tool = _tool(
            FakeArchitectAsker(
                answer="ok",
                tool_calls_made=None,
                successful_tool_calls=None,
                failed_tool_calls=None,
            ),
            reply,
        )

        await tool.handler(ConsultAgentInput(prompt="hi"))

        self.assertEqual(reply.fields[1], ())

    async def test_reply_announcement_omits_only_the_unreported_tool_call_fields(self) -> None:
        """Each field is omitted independently -- an agent may report some
        counts but not others (e.g. a `tool_calls_made` total with no
        success/failure breakdown yet)."""

        reply = FakeReplySender()
        tool = _tool(
            FakeArchitectAsker(
                answer="ok",
                tool_calls_made=3,
                successful_tool_calls=None,
                failed_tool_calls=None,
            ),
            reply,
        )

        await tool.handler(ConsultAgentInput(prompt="hi"))

        self.assertEqual(len(reply.fields[1]), 1)
        self.assertEqual(reply.fields[1][0].name, "Tool calls")
        self.assertEqual(reply.fields[1][0].value, "3")

    async def test_announces_the_question_then_a_failure(self) -> None:
        reply = FakeReplySender()
        tool = _tool(FakeArchitectAsker(fail_with=ArchitectRequestError("unreachable")), reply)

        await tool.handler(ConsultAgentInput(prompt="anything"))

        self.assertEqual(len(reply.replies), 2)
        self.assertIn("anything", reply.replies[0] or "")
        self.assertIn("unreachable", reply.replies[1] or "")

    async def test_footer_override_carries_the_consulted_agents_icon(self) -> None:
        reply = FakeReplySender()
        tool = _tool(
            FakeArchitectAsker(answer="ok"),
            reply,
            footer_icon_path=Path("/architect/assets/avatar.png"),
        )

        await tool.handler(ConsultAgentInput(prompt="hi"))

        self.assertEqual(len(reply.footer_overrides), 2)
        for override in reply.footer_overrides:
            self.assertEqual(override.name, "architect")
            self.assertEqual(override.icon_filename, "avatar.png")
        # The real local Path is forwarded alongside the override on every
        # call, not just used to derive icon_filename -- this is what lets
        # build_reply_payload actually attach the file (see
        # docs/reply-identity-design.md section 7 on why this is an
        # attachment, not a URL corridor's shared A2A listener serves).
        self.assertEqual(reply.footer_icon_paths, [Path("/architect/assets/avatar.png")] * 2)

    async def test_no_footer_override_without_an_icon_path(self) -> None:
        reply = FakeReplySender()
        tool = _tool(FakeArchitectAsker(answer="ok"), reply)

        await tool.handler(ConsultAgentInput(prompt="hi"))

        self.assertEqual(reply.footer_overrides, [None, None])

    async def test_a_broken_announcement_does_not_fail_the_tool_call(self) -> None:
        class BrokenReplySender(FakeReplySender):
            async def send_reply(self, ctx: object, **kwargs: Any) -> None:
                raise RuntimeError("channel gone")

        tool = _tool(FakeArchitectAsker(answer="fine"), BrokenReplySender())

        output = await tool.handler(ConsultAgentInput(prompt="anything"))

        assert isinstance(output, ConsultAgentOutput)
        self.assertEqual(output.status, "ok")
        self.assertEqual(output.answer, "fine")


class TestConsultAgentToolOfficeVisualization(unittest.IsolatedAsyncioTestCase):
    """Alongside the Discord announcements, the tool also publishes
    `AgentReplied` events so floorplan's office dashboard shows the same
    exchange as activity bubbles -- see this tool's own module docstring
    and docs/office-agent-identity-design.md."""

    async def test_publishes_the_question_attributed_to_picos_own_identity(self) -> None:
        corridor = FakeCorridor()
        tool = _tool(
            FakeArchitectAsker(answer="ok"),
            FakeReplySender(),
            corridor=corridor,
            guild_id=42,
            bot_user_id=7,
        )

        await tool.handler(ConsultAgentInput(prompt="what furniture exists?"))

        asking = corridor.published[0]
        self.assertEqual(asking.agent.discord_user_id, 7)
        self.assertEqual(asking.agent.guild_id, 42)
        self.assertTrue(asking.agent.is_bot)
        self.assertIsNone(asking.agent.agent_key)
        self.assertIn("what furniture exists?", asking.summary)
        self.assertIn("architect", asking.summary)

    async def test_publishes_the_answer_attributed_to_the_consulted_agents_genuine_identity(
        self,
    ) -> None:
        corridor = FakeCorridor()
        tool = _tool(
            FakeArchitectAsker(answer="38 items total"),
            FakeReplySender(),
            corridor=corridor,
            agent_key="architect",
        )

        await tool.handler(ConsultAgentInput(prompt="what furniture exists?"))

        self.assertEqual(len(corridor.published), 2)
        replied = corridor.published[1]
        self.assertIsNone(replied.agent.discord_user_id)
        self.assertIsNone(replied.agent.guild_id)
        self.assertTrue(replied.agent.is_bot)
        self.assertEqual(replied.agent.agent_key, "architect")
        self.assertEqual(replied.summary, "38 items total")

    async def test_a_failure_only_publishes_the_question_not_a_reply(self) -> None:
        corridor = FakeCorridor()
        tool = _tool(
            FakeArchitectAsker(fail_with=ArchitectRequestError("unreachable")),
            FakeReplySender(),
            corridor=corridor,
        )

        await tool.handler(ConsultAgentInput(prompt="anything"))

        self.assertEqual(len(corridor.published), 1)

    async def test_no_question_event_without_a_bot_user_id(self) -> None:
        corridor = FakeCorridor()
        tool = _tool(
            FakeArchitectAsker(answer="ok"),
            FakeReplySender(),
            corridor=corridor,
            bot_user_id=None,
        )

        await tool.handler(ConsultAgentInput(prompt="hi"))

        self.assertEqual(len(corridor.published), 1)
        self.assertEqual(corridor.published[0].agent.agent_key, "architect")

    async def test_a_broken_publish_does_not_fail_the_tool_call(self) -> None:
        class BrokenCorridor(FakeCorridor):
            async def publish_event(self, event: object) -> None:
                raise RuntimeError("bus gone")

        tool = _tool(
            FakeArchitectAsker(answer="fine"), FakeReplySender(), corridor=BrokenCorridor()
        )

        output = await tool.handler(ConsultAgentInput(prompt="anything"))

        assert isinstance(output, ConsultAgentOutput)
        self.assertEqual(output.status, "ok")
        self.assertEqual(output.answer, "fine")


if __name__ == "__main__":
    unittest.main()
