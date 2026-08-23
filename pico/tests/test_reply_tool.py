"""Pydantic Input/Output validation, plus a FakeCorridor double for the
handler's `corridor.send_reply` call -- the only Discord-send in the whole
cog."""

from __future__ import annotations

import unittest
from typing import Any

import pytest
from pydantic import ValidationError

from corridor.domain import AgentRef, AgentReplied, ReplyField

from ..tools.reply_tool import ReplyFieldInput, ReplyInput, ReplyOutput, ReplyTool


class FakeSentMessage:
    def __init__(self, message_id: int) -> None:
        self.id = message_id


class FakeCorridor:
    def __init__(
        self, *, fail_with: Exception | None = None, publish_fails_with: Exception | None = None
    ) -> None:
        self.fail_with = fail_with
        self.publish_fails_with = publish_fails_with
        self.calls: list[dict[str, Any]] = []
        self.published: list[object] = []
        self._next_id = 1

    async def send_reply(
        self,
        ctx: object,
        *,
        title: str | None = None,
        description: str | None = None,
        content: str | None = None,
        fields: Any = (),
    ) -> FakeSentMessage:
        self.calls.append(
            {
                "ctx": ctx,
                "title": title,
                "description": description,
                "content": content,
                "fields": list(fields),
            }
        )
        if self.fail_with is not None:
            raise self.fail_with
        message = FakeSentMessage(self._next_id)
        self._next_id += 1
        return message

    async def publish_event(self, event: object) -> None:
        if self.publish_fails_with is not None:
            raise self.publish_fails_with
        self.published.append(event)


class TestReplyInputValidation(unittest.TestCase):
    def test_defaults(self) -> None:
        parsed = ReplyInput.model_validate({})

        self.assertIsNone(parsed.content)
        self.assertIsNone(parsed.title)
        self.assertIsNone(parsed.description)
        self.assertEqual(parsed.fields, [])

    def test_parses_fields(self) -> None:
        parsed = ReplyInput.model_validate(
            {"description": "hi", "fields": [{"name": "n", "value": "v"}]}
        )

        self.assertEqual(parsed.fields, [ReplyFieldInput(name="n", value="v", inline=True)])

    def test_rejects_an_incomplete_field(self) -> None:
        with pytest.raises(ValidationError):
            ReplyInput.model_validate({"fields": [{"name": "n"}]})


class TestReplyOutputValidation(unittest.TestCase):
    def test_round_trips_through_json(self) -> None:
        output = ReplyOutput(sent=True, message_id=42, error=None)

        self.assertEqual(ReplyOutput.model_validate_json(output.model_dump_json()), output)


class TestReplyToolHandler(unittest.IsolatedAsyncioTestCase):
    async def test_sends_through_corridor_and_reports_the_message_id(self) -> None:
        corridor = FakeCorridor()
        ctx = object()
        tool = ReplyTool(corridor, ctx, guild_id=100, bot_user_id=999)

        output = await tool.handler(
            ReplyInput(
                description="hello", fields=[ReplyFieldInput(name="a", value="b", inline=False)]
            )
        )

        assert isinstance(output, ReplyOutput)
        self.assertTrue(output.sent)
        self.assertEqual(output.message_id, 1)
        self.assertIsNone(output.error)
        self.assertEqual(corridor.calls[0]["ctx"], ctx)
        self.assertEqual(corridor.calls[0]["description"], "hello")
        self.assertEqual(corridor.calls[0]["fields"], [ReplyField("a", "b", False)])

    async def test_reports_corridor_failures_as_a_failed_output_instead_of_raising(self) -> None:
        corridor = FakeCorridor(fail_with=RuntimeError("discord is down"))
        tool = ReplyTool(corridor, object(), guild_id=100, bot_user_id=999)

        output = await tool.handler(ReplyInput(content="hi"))

        assert isinstance(output, ReplyOutput)
        self.assertFalse(output.sent)
        self.assertIsNone(output.message_id)
        self.assertEqual(output.error, "discord is down")

    async def test_a_failed_send_does_not_publish_agent_replied(self) -> None:
        corridor = FakeCorridor(fail_with=RuntimeError("discord is down"))
        tool = ReplyTool(corridor, object(), guild_id=100, bot_user_id=999)

        await tool.handler(ReplyInput(content="hi"))

        self.assertEqual(corridor.published, [])


class TestReplyToolPublishesAgentReplied(unittest.IsolatedAsyncioTestCase):
    """The reply tool is now a direct corridor bus publisher, closing the
    loop into floorplan's canvas rendering without going through floorplan's
    own on_message listener (which now explicitly ignores this bot's own
    messages to avoid a duplicate publish -- see
    floorplan/adapters/discord_gateway.py)."""

    async def test_successful_send_publishes_agent_replied(self) -> None:
        corridor = FakeCorridor()
        tool = ReplyTool(corridor, object(), guild_id=100, bot_user_id=999)

        await tool.handler(ReplyInput(content="hello world"))

        self.assertEqual(
            corridor.published,
            [
                AgentReplied(
                    agent=AgentRef(discord_user_id=999, guild_id=100, is_bot=True),
                    summary="hello world",
                )
            ],
        )

    async def test_summary_falls_back_to_description_then_title(self) -> None:
        corridor = FakeCorridor()
        tool = ReplyTool(corridor, object(), guild_id=100, bot_user_id=999)

        await tool.handler(ReplyInput(description="a description"))

        self.assertEqual(corridor.published[0].summary, "a description")

    async def test_no_bot_user_id_skips_publishing(self) -> None:
        corridor = FakeCorridor()
        tool = ReplyTool(corridor, object(), guild_id=100, bot_user_id=None)

        output = await tool.handler(ReplyInput(content="hi"))

        assert isinstance(output, ReplyOutput)
        self.assertTrue(output.sent)
        self.assertEqual(corridor.published, [])

    async def test_publish_failure_does_not_affect_the_reported_send_result(self) -> None:
        corridor = FakeCorridor(publish_fails_with=RuntimeError("bus is down"))
        tool = ReplyTool(corridor, object(), guild_id=100, bot_user_id=999)

        output = await tool.handler(ReplyInput(content="hi"))

        assert isinstance(output, ReplyOutput)
        self.assertTrue(output.sent)
        self.assertEqual(output.message_id, 1)
        self.assertIsNone(output.error)
