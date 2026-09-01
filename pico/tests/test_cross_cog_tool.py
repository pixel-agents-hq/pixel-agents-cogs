"""CrossCogTool is fully testable without Red: a plain RegisteredTool with
an async dict-in/dict-out handler stands in for a real cross-cog
registration, no unittest.mock needed. A small local FakeCorridor
(matching test_reply_tool.py's own) covers `publish_event` -- CrossCogTool
publishes `AgentReplied` after a successful call, same convention
`ReplyTool` follows."""

from __future__ import annotations

import unittest
from typing import Any

from corridor.domain import AgentRef, AgentReplied, RegisteredTool

from ..tools.cross_cog import CrossCogTool

_PARAMETERS = {
    "type": "object",
    "properties": {"timezone": {"type": "string", "description": "An IANA zone name."}},
    "required": [],
}


class FakeCorridor:
    def __init__(self, *, publish_fails_with: Exception | None = None) -> None:
        self.publish_fails_with = publish_fails_with
        self.published: list[object] = []

    async def publish_event(self, event: object) -> None:
        if self.publish_fails_with is not None:
            raise self.publish_fails_with
        self.published.append(event)


def _echo_handler(received_ctx: list[object]) -> object:
    async def handler(ctx: object, raw_input: dict) -> dict:
        received_ctx.append(ctx)
        return {"received": raw_input}

    return handler


def _tool(**overrides: object) -> RegisteredTool:
    defaults: dict[str, object] = {
        "name": "deskutils_time",
        "description": "Get the current time.",
        "parameters": _PARAMETERS,
        "handler": _echo_handler([]),
        "required_group": "employee",
    }
    defaults.update(overrides)
    return RegisteredTool(**defaults)  # type: ignore[arg-type]


def _adapt(
    tool: RegisteredTool, *, corridor: Any | None = None, ctx: object = None
) -> CrossCogTool:
    return CrossCogTool(
        tool,
        ctx if ctx is not None else object(),
        corridor=corridor if corridor is not None else FakeCorridor(),
        guild_id=100,
        bot_user_id=999,
    )


class TestCrossCogTool(unittest.IsolatedAsyncioTestCase):
    def test_metadata_passes_through(self) -> None:
        adapted = _adapt(_tool())

        self.assertEqual(adapted.name, "deskutils_time")
        self.assertEqual(adapted.description, "Get the current time.")

    def test_input_schema_matches_the_registered_parameters_verbatim(self) -> None:
        adapted = _adapt(_tool())

        self.assertEqual(adapted.Input.model_json_schema(), _PARAMETERS)

    def test_input_accepts_arbitrary_argument_shapes(self) -> None:
        adapted = _adapt(_tool())

        parsed = adapted.Input.model_validate({"timezone": "America/New_York", "extra": 1})

        self.assertEqual(parsed.model_dump(), {"timezone": "America/New_York", "extra": 1})

    async def test_handler_round_trips_the_wrapped_tools_result(self) -> None:
        adapted = _adapt(_tool())

        output = await adapted.handler(adapted.Input.model_validate({"timezone": "UTC"}))

        self.assertEqual(output.model_dump(), {"received": {"timezone": "UTC"}})

    async def test_handler_passes_the_turns_ctx_through_to_the_wrapped_tool(self) -> None:
        received_ctx: list[object] = []
        ctx = object()
        adapted = _adapt(_tool(handler=_echo_handler(received_ctx)), ctx=ctx)

        await adapted.handler(adapted.Input.model_validate({}))

        self.assertEqual(received_ctx, [ctx])

    def test_a_non_mapping_parameters_raises_at_construction(self) -> None:
        with self.assertRaises(TypeError):
            _adapt(_tool(parameters=None))


class TestCrossCogToolPublishesAgentReplied(unittest.IsolatedAsyncioTestCase):
    """Closes the same gap ReplyTool/ConsultAgentTool already closed for
    their own tool shapes: pico is the one actually calling the tool (the
    registering cog only supplied the handler), so pico is the one that
    publishes -- CCTV subscribes and renders whatever the bus delivers."""

    async def test_successful_call_publishes_agent_replied(self) -> None:
        corridor = FakeCorridor()
        adapted = _adapt(_tool(), corridor=corridor)

        await adapted.handler(adapted.Input.model_validate({"timezone": "UTC"}))

        self.assertEqual(
            corridor.published,
            [
                AgentReplied(
                    agent=AgentRef(discord_user_id=999, guild_id=100, is_bot=True),
                    summary="using tool deskutils_time",
                )
            ],
        )

    async def test_no_bot_user_id_skips_publishing(self) -> None:
        corridor = FakeCorridor()
        adapted = CrossCogTool(_tool(), object(), corridor=corridor, guild_id=100, bot_user_id=None)

        await adapted.handler(adapted.Input.model_validate({}))

        self.assertEqual(corridor.published, [])

    async def test_publish_failure_does_not_affect_the_reported_result(self) -> None:
        corridor = FakeCorridor(publish_fails_with=RuntimeError("bus is down"))
        adapted = _adapt(_tool(), corridor=corridor)

        output = await adapted.handler(adapted.Input.model_validate({"timezone": "UTC"}))

        self.assertEqual(output.model_dump(), {"received": {"timezone": "UTC"}})


if __name__ == "__main__":
    unittest.main()
