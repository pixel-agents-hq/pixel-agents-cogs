"""Pydantic Input/Output validation, plus a FakeCorridor double for the
handler's `corridor.send_reply` call -- the only Discord-send in the whole
cog."""

from __future__ import annotations

import unittest
from typing import Any

import pytest
from pydantic import ValidationError

from corridor.domain import ReplyField

from ..tools.reply_tool import ReplyFieldInput, ReplyInput, ReplyOutput, ReplyTool


class FakeSentMessage:
    def __init__(self, message_id: int) -> None:
        self.id = message_id


class FakeCorridor:
    def __init__(self, *, fail_with: Exception | None = None) -> None:
        self.fail_with = fail_with
        self.calls: list[dict[str, Any]] = []
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
        tool = ReplyTool(corridor, ctx)

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
        tool = ReplyTool(corridor, object())

        output = await tool.handler(ReplyInput(content="hi"))

        assert isinstance(output, ReplyOutput)
        self.assertFalse(output.sent)
        self.assertIsNone(output.message_id)
        self.assertEqual(output.error, "discord is down")
