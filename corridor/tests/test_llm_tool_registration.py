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
    def __init__(
        self,
        callback: object,
        *,
        qualified_name: str = "stub command",
        allowed: bool = True,
    ) -> None:
        self.callback = callback
        self.qualified_name = qualified_name
        self.allowed = allowed
        self.can_run_calls: list[tuple[object, bool]] = []

    async def can_run(self, ctx: object, *, check_all_parents: bool = False) -> bool:
        self.can_run_calls.append((ctx, check_all_parents))
        return self.allowed


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


@llm_tool(name="informational_tool", description="Returns information.")
async def _informational_command(
    cog: object, ctx: object, value: str | None = None
) -> dict[str, object]:
    return {"status": "ok", "received": value}


@llm_tool(name="invalid_output_tool", description="Returns the wrong type.")
async def _invalid_output_command(cog: object, ctx: object) -> str:
    return "not a mapping"


@llm_tool(name="invalid_key_tool", description="Returns the wrong key type.")
async def _invalid_key_command(cog: object, ctx: object) -> dict[object, object]:
    return {1: "not a string key"}


@llm_tool()
async def _inferred_command(cog: object, ctx: object, text: str) -> None:
    """Count the supplied text."""


class _CogWithInformationalTool:
    def __init__(self) -> None:
        self.informational_command = _StubCommand(_informational_command)


class _CogWithInvalidOutputTool:
    def __init__(self) -> None:
        self.invalid_output_command = _StubCommand(_invalid_output_command)


class _CogWithInvalidKeyTool:
    def __init__(self) -> None:
        self.invalid_key_command = _StubCommand(_invalid_key_command)


class _CogWithInferredTool:
    def __init__(self, *, allowed: bool = True) -> None:
        self.inferred_command = _StubCommand(
            _inferred_command,
            qualified_name="deskutils count",
            allowed=allowed,
        )


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
            {
                "type": "object",
                "properties": {"value": {"type": "string", "description": "value for value"}},
                "required": [],
            },
        )
        self.assertIsNone(tool.availability_check)

    async def test_missing_metadata_is_inferred_from_the_command(self) -> None:
        cog = _CogWithInferredTool()
        tool = collect_registered_tools(cog)[0]
        ctx = object()

        self.assertEqual(tool.name, "deskutils_count")
        self.assertEqual(tool.description, "Count the supplied text.")
        self.assertEqual(
            tool.parameters,
            {
                "type": "object",
                "properties": {"text": {"type": "string", "description": "value for text"}},
                "required": ["text"],
            },
        )
        assert tool.availability_check is not None
        self.assertTrue(await tool.availability_check(ctx))
        self.assertEqual(cog.inferred_command.can_run_calls, [(ctx, True)])

    async def test_the_built_handler_invokes_the_callback_with_cog_and_ctx(self) -> None:
        cog = _CogWithOneTool()
        tool = collect_registered_tools(cog)[0]
        ctx = object()

        result = await tool.handler(ctx, {"value": "hi"})

        self.assertEqual(cog.calls, [(cog, ctx, {"value": "hi"})])
        self.assertEqual(result, {"status": "ok"})

    async def test_the_built_handler_forwards_an_informational_mapping(self) -> None:
        tool = collect_registered_tools(_CogWithInformationalTool())[0]

        result = await tool.handler(object(), {"value": "hi"})

        self.assertEqual(result, {"status": "ok", "received": "hi"})

    async def test_the_built_handler_rejects_a_non_mapping_result(self) -> None:
        tool = collect_registered_tools(_CogWithInvalidOutputTool())[0]

        with self.assertRaisesRegex(TypeError, "expected a mapping or None"):
            await tool.handler(object(), {})

    async def test_the_built_handler_rejects_a_mapping_with_non_string_keys(self) -> None:
        tool = collect_registered_tools(_CogWithInvalidKeyTool())[0]

        with self.assertRaisesRegex(TypeError, "mapping with a non-string key"):
            await tool.handler(object(), {})

    def test_a_callback_reachable_under_two_names_is_only_registered_once(self) -> None:
        cog = _CogWithOneTool()
        cog.alias_for_time_command = cog.time_command  # type: ignore[attr-defined]

        tools = collect_registered_tools(cog)

        self.assertEqual(len(tools), 1)


if __name__ == "__main__":
    unittest.main()
