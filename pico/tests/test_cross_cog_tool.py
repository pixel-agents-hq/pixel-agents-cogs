"""CrossCogTool is fully testable without Red: a plain RegisteredTool with
an async dict-in/dict-out handler stands in for a real cross-cog
registration, no unittest.mock needed."""

from __future__ import annotations

import unittest

from corridor.domain import RegisteredTool

from ..tools.cross_cog import CrossCogTool

_PARAMETERS = {
    "type": "object",
    "properties": {"timezone": {"type": "string", "description": "An IANA zone name."}},
    "required": [],
}


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


class TestCrossCogTool(unittest.IsolatedAsyncioTestCase):
    def test_metadata_passes_through(self) -> None:
        adapted = CrossCogTool(_tool(), ctx=object())

        self.assertEqual(adapted.name, "deskutils_time")
        self.assertEqual(adapted.description, "Get the current time.")

    def test_input_schema_matches_the_registered_parameters_verbatim(self) -> None:
        adapted = CrossCogTool(_tool(), ctx=object())

        self.assertEqual(adapted.Input.model_json_schema(), _PARAMETERS)

    def test_input_accepts_arbitrary_argument_shapes(self) -> None:
        adapted = CrossCogTool(_tool(), ctx=object())

        parsed = adapted.Input.model_validate({"timezone": "America/New_York", "extra": 1})

        self.assertEqual(parsed.model_dump(), {"timezone": "America/New_York", "extra": 1})

    async def test_handler_round_trips_the_wrapped_tools_result(self) -> None:
        adapted = CrossCogTool(_tool(), ctx=object())

        output = await adapted.handler(adapted.Input.model_validate({"timezone": "UTC"}))

        self.assertEqual(output.model_dump(), {"received": {"timezone": "UTC"}})

    async def test_handler_passes_the_turns_ctx_through_to_the_wrapped_tool(self) -> None:
        received_ctx: list[object] = []
        ctx = object()
        adapted = CrossCogTool(_tool(handler=_echo_handler(received_ctx)), ctx)

        await adapted.handler(adapted.Input.model_validate({}))

        self.assertEqual(received_ctx, [ctx])

    def test_a_non_mapping_parameters_raises_at_construction(self) -> None:
        with self.assertRaises(TypeError):
            CrossCogTool(_tool(parameters=None), ctx=object())


if __name__ == "__main__":
    unittest.main()
