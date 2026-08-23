"""ToolRegistryService is fully testable without Red: plain RegisteredTool
values and async handler closures stand in for a real registration, no
unittest.mock needed."""

from __future__ import annotations

import unittest

from ..application import ToolRegistryService
from ..domain import RegisteredTool


async def _handler(raw_input: object) -> dict[str, object]:
    return {}


def _tool(name: str, *, required_group: str | None = None) -> RegisteredTool:
    return RegisteredTool(
        name=name,
        description="A tool.",
        parameters={"type": "object", "properties": {}},
        handler=_handler,
        required_group=required_group,
    )


class TestToolRegistryService(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = ToolRegistryService()

    def test_list_tools_with_nothing_registered_is_empty(self) -> None:
        self.assertEqual(self.registry.list_tools(), ())

    def test_registered_tool_is_listed(self) -> None:
        tool = _tool("a")
        self.registry.register(tool, owner="A")

        self.assertEqual(self.registry.list_tools(), (tool,))

    def test_multiple_owners_tools_are_all_listed(self) -> None:
        # RegisteredTool isn't hashable (its `parameters`/`handler` fields
        # aren't) -- compare names instead of using a set.
        a = _tool("a")
        b = _tool("b")
        self.registry.register(a, owner="A")
        self.registry.register(b, owner="B")

        self.assertEqual({tool.name for tool in self.registry.list_tools()}, {"a", "b"})

    def test_same_owner_reregistration_overwrites(self) -> None:
        first = _tool("a", required_group=None)
        second = _tool("a", required_group="employee")
        self.registry.register(first, owner="A")

        self.registry.register(second, owner="A")

        self.assertEqual(self.registry.list_tools(), (second,))

    def test_different_owner_name_collision_raises(self) -> None:
        self.registry.register(_tool("a"), owner="A")

        with self.assertRaises(ValueError):
            self.registry.register(_tool("a"), owner="B")

    def test_unregister_owner_drops_only_that_owners_tools(self) -> None:
        a = _tool("a")
        b = _tool("b")
        self.registry.register(a, owner="A")
        self.registry.register(b, owner="B")

        self.registry.unregister_owner("A")

        self.assertEqual(self.registry.list_tools(), (b,))

    def test_unregister_owner_for_unknown_owner_is_a_noop(self) -> None:
        self.registry.unregister_owner("nobody")  # must not raise


if __name__ == "__main__":
    unittest.main()
