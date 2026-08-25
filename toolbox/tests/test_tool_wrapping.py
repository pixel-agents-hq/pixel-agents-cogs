"""collect_wrappable_tools is fully testable without Red -- same shape as
corridor's own test_llm_tool_registration.py, which this mirrors."""

from __future__ import annotations

import unittest
from typing import Any

from corridor.domain import llm_tool

from ..adapters.tool_wrapping import collect_wrappable_tools


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


async def _plain_command(cog: Any, ctx: object, name: str, count: int = 1) -> None:
    """Do something with a name and a count."""
    cog.calls.append((cog, ctx, {"name": name, "count": count}))


class _CogWithOnePlainCommand:
    def __init__(self) -> None:
        self.calls: list[tuple[object, object, dict[str, object]]] = []
        self.some_command = _StubCommand(_plain_command, qualified_name="toolbox greet")


async def _no_docstring_command(cog: object, ctx: object) -> None: ...


class _CogWithNoDocstringCommand:
    def __init__(self) -> None:
        self.some_command = _StubCommand(_no_docstring_command, qualified_name="toolbox ping")


async def _informational_command(cog: object, ctx: object, value: str) -> dict[str, object]:
    """Echo a value back."""
    return {"status": "ok", "received": value}


class _CogWithInformationalCommand:
    def __init__(self) -> None:
        self.some_command = _StubCommand(_informational_command, qualified_name="toolbox echo")


@llm_tool(name="already_a_tool", description="Already decorated.")
async def _decorated_command(cog: object, ctx: object) -> None: ...


class _CogWithADecoratedCommand:
    def __init__(self) -> None:
        self.decorated_command = _StubCommand(
            _decorated_command, qualified_name="toolbox decorated"
        )


class _PlainCog:
    def __init__(self) -> None:
        self.not_a_command = "just a string"


class TestCollectWrappableTools(unittest.IsolatedAsyncioTestCase):
    def test_a_cog_with_no_commands_yields_nothing(self) -> None:
        self.assertEqual(collect_wrappable_tools(_PlainCog(), frozenset({"anything"})), [])

    def test_a_command_not_in_the_selected_set_is_skipped(self) -> None:
        cog = _CogWithOnePlainCommand()

        tools = collect_wrappable_tools(cog, frozenset({"some other command"}))

        self.assertEqual(tools, [])

    def test_a_selected_command_is_wrapped(self) -> None:
        cog = _CogWithOnePlainCommand()

        tools = collect_wrappable_tools(cog, frozenset({"toolbox greet"}))

        self.assertEqual(len(tools), 1)
        tool = tools[0]
        self.assertEqual(tool.name, "toolbox_greet")
        self.assertEqual(tool.description, "Do something with a name and a count.")
        self.assertIsNone(tool.required_group)
        self.assertEqual(
            tool.parameters,
            {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "value for name"},
                    "count": {"type": "integer", "description": "value for count"},
                },
                "required": ["name"],
            },
        )

    def test_an_already_decorated_command_is_never_wrapped(self) -> None:
        cog = _CogWithADecoratedCommand()

        tools = collect_wrappable_tools(cog, frozenset({"toolbox decorated"}))

        self.assertEqual(tools, [])

    def test_a_docstring_less_command_gets_a_generic_description(self) -> None:
        cog = _CogWithNoDocstringCommand()

        tools = collect_wrappable_tools(cog, frozenset({"toolbox ping"}))

        self.assertEqual(len(tools), 1)
        self.assertEqual(tools[0].description, "Run the Discord command `toolbox ping`.")

    async def test_availability_check_delegates_to_can_run(self) -> None:
        cog = _CogWithOnePlainCommand()
        ctx = object()

        tool = collect_wrappable_tools(cog, frozenset({"toolbox greet"}))[0]

        assert tool.availability_check is not None
        self.assertTrue(await tool.availability_check(ctx))
        self.assertEqual(cog.some_command.can_run_calls, [(ctx, True)])

    async def test_the_built_handler_invokes_the_callback_with_cog_and_ctx(self) -> None:
        cog = _CogWithOnePlainCommand()
        ctx = object()

        tool = collect_wrappable_tools(cog, frozenset({"toolbox greet"}))[0]
        result = await tool.handler(ctx, {"name": "Ada", "count": 2})

        self.assertEqual(cog.calls, [(cog, ctx, {"name": "Ada", "count": 2})])
        self.assertEqual(result, {"status": "ok"})

    async def test_the_built_handler_forwards_an_informational_mapping(self) -> None:
        cog = _CogWithInformationalCommand()

        tool = collect_wrappable_tools(cog, frozenset({"toolbox echo"}))[0]
        result = await tool.handler(object(), {"value": "hi"})

        self.assertEqual(result, {"status": "ok", "received": "hi"})

    def test_a_callback_reachable_under_two_names_is_only_wrapped_once(self) -> None:
        cog = _CogWithOnePlainCommand()
        cog.alias_for_some_command = cog.some_command  # type: ignore[attr-defined]

        tools = collect_wrappable_tools(cog, frozenset({"toolbox greet"}))

        self.assertEqual(len(tools), 1)


if __name__ == "__main__":
    unittest.main()
