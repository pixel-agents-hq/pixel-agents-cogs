"""AgentToolServerTool -- adapts a corridor RegisteredTool into bootcamp's
own ToolSpec. Mirrors pico/tests/test_cross_cog.py's own coverage of
CrossCogTool, the structurally-identical twin this was copied from."""

from __future__ import annotations

import unittest
from collections.abc import Mapping

from corridor.domain import RegisteredTool

from ..tools.agent_tool_server import AgentToolServerTool


async def _handler(_ctx: object, arguments: Mapping[str, object]) -> Mapping[str, object]:
    return {"echoed": arguments.get("text")}


def _tool(name: str = "report_error") -> RegisteredTool:
    return RegisteredTool(
        name=name,
        description="Report an error.",
        parameters={"type": "object", "properties": {"text": {"type": "string"}}},
        handler=_handler,
    )


class TestAgentToolServerTool(unittest.IsolatedAsyncioTestCase):
    def test_name_and_description_come_from_the_registered_tool(self) -> None:
        tool = AgentToolServerTool(_tool())

        self.assertEqual(tool.name, "report_error")
        self.assertEqual(tool.description, "Report an error.")

    def test_input_schema_is_the_registered_tools_parameters_verbatim(self) -> None:
        tool = AgentToolServerTool(_tool())

        self.assertEqual(
            tool.Input.model_json_schema(),
            {"type": "object", "properties": {"text": {"type": "string"}}},
        )

    async def test_handler_calls_through_with_a_none_ctx_and_wraps_the_result(self) -> None:
        tool = AgentToolServerTool(_tool())
        raw_input = tool.Input.model_validate({"text": "hi"})

        output = await tool.handler(raw_input)

        self.assertEqual(output.model_dump(), {"echoed": "hi"})

    async def test_handler_passes_none_as_ctx(self) -> None:
        seen_ctx: list[object] = []

        async def handler(ctx: object, arguments: Mapping[str, object]) -> Mapping[str, object]:
            seen_ctx.append(ctx)
            return {}

        tool = AgentToolServerTool(
            RegisteredTool(
                name="x", description="x", parameters={"type": "object"}, handler=handler
            )
        )

        await tool.handler(tool.Input.model_validate({}))

        self.assertEqual(seen_ctx, [None])


if __name__ == "__main__":
    unittest.main()
