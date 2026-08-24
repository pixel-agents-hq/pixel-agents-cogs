"""collect_registered_tools is fully testable without Red: a tiny
_StubCommand (just a `.callback` attribute) stands in for both a real
discord.py Command and the redbot test stub's _FakeCommand -- both expose
`.callback` the same way, which is the only thing this scanner reads."""

from __future__ import annotations

import unittest
from typing import Any

from ..adapters.llm_tool_registration import collect_registered_tools
from ..domain import llm_tool


class _StubCommand:
    def __init__(self, callback: object) -> None:
        self.callback = callback


class _PlainCog:
    """A command-free cog: nothing here should ever be picked up."""

    def __init__(self) -> None:
        self.not_a_command = "just a string"


@llm_tool(name="a_tool", description="Does a thing.", required_group="employee")
async def _command(cog: Any, ctx: object, value: str | None = None) -> None:
    cog.calls.append((cog, ctx, {"value": value}))


class _CogWithOneTool:
    def __init__(self) -> None:
        self.calls: list[tuple[object, object, dict[str, object]]] = []
        self.time_command = _StubCommand(_command)


class TestCollectRegisteredTools(unittest.IsolatedAsyncioTestCase):
    def test_a_cog_with_nothing_decorated_yields_nothing(self) -> None:
        self.assertEqual(collect_registered_tools(_PlainCog()), [])

    def test_a_decorated_command_is_found(self) -> None:
        tools = collect_registered_tools(_CogWithOneTool())

        self.assertEqual(len(tools), 1)
        tool = tools[0]
        self.assertEqual(tool.name, "a_tool")
        self.assertEqual(tool.description, "Does a thing.")
        self.assertEqual(tool.required_group, "employee")
        self.assertEqual(
            tool.parameters,
            {"type": "object", "properties": {"value": {"type": "string"}}, "required": []},
        )

    async def test_the_built_handler_invokes_the_callback_with_cog_and_ctx(self) -> None:
        cog = _CogWithOneTool()
        tool = collect_registered_tools(cog)[0]
        ctx = object()

        result = await tool.handler(ctx, {"value": "hi"})

        self.assertEqual(cog.calls, [(cog, ctx, {"value": "hi"})])
        self.assertEqual(result, {"status": "ok"})

    def test_a_callback_reachable_under_two_names_is_only_registered_once(self) -> None:
        cog = _CogWithOneTool()
        cog.alias_for_time_command = cog.time_command  # type: ignore[attr-defined]

        tools = collect_registered_tools(cog)

        self.assertEqual(len(tools), 1)


if __name__ == "__main__":
    unittest.main()
