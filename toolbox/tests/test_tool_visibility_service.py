"""ToolVisibilityService is fully testable without Red: a plain in-memory
fake satisfies the ToolVisibilityRepository protocol, same pattern as
test_tool_selection_service.py's FakeToolSelectionRepository."""

from __future__ import annotations

import unittest

from ..application import ToolVisibilityService


class FakeToolVisibilityRepository:
    def __init__(self) -> None:
        self._defaults: dict[str, bool] = {}
        self._overrides: dict[tuple[int, str], bool] = {}

    async def get_default(self, tool_name: str) -> bool | None:
        return self._defaults.get(tool_name)

    async def set_default(self, tool_name: str, enabled: bool) -> None:
        self._defaults[tool_name] = enabled

    async def get_override(self, guild_id: int, tool_name: str) -> bool | None:
        return self._overrides.get((guild_id, tool_name))

    async def set_override(self, guild_id: int, tool_name: str, enabled: bool) -> None:
        self._overrides[(guild_id, tool_name)] = enabled

    async def clear_override(self, guild_id: int, tool_name: str) -> None:
        self._overrides.pop((guild_id, tool_name), None)


class TestToolVisibilityService(unittest.IsolatedAsyncioTestCase):
    async def test_a_tool_with_no_default_and_no_override_is_visible(self) -> None:
        service = ToolVisibilityService(FakeToolVisibilityRepository())

        self.assertTrue(await service.is_enabled("a_tool", guild_id=10))

    async def test_a_tool_with_no_guild_context_falls_back_to_the_default(self) -> None:
        service = ToolVisibilityService(FakeToolVisibilityRepository())
        await service.set_default("a_tool", False)

        self.assertFalse(await service.is_enabled("a_tool", guild_id=None))

    async def test_a_disabled_default_applies_to_every_guild(self) -> None:
        service = ToolVisibilityService(FakeToolVisibilityRepository())
        await service.set_default("a_tool", False)

        self.assertFalse(await service.is_enabled("a_tool", guild_id=10))
        self.assertFalse(await service.is_enabled("a_tool", guild_id=20))

    async def test_a_guild_override_wins_over_the_global_default(self) -> None:
        service = ToolVisibilityService(FakeToolVisibilityRepository())
        await service.set_default("a_tool", False)
        await service.set_override(10, "a_tool", True)

        self.assertTrue(await service.is_enabled("a_tool", guild_id=10))
        self.assertFalse(await service.is_enabled("a_tool", guild_id=20))

    async def test_get_override_distinguishes_no_override_from_a_false_override(self) -> None:
        service = ToolVisibilityService(FakeToolVisibilityRepository())

        self.assertIsNone(await service.get_override(10, "a_tool"))

        await service.set_override(10, "a_tool", False)

        self.assertEqual(await service.get_override(10, "a_tool"), False)

    async def test_clearing_an_override_falls_back_to_the_default_again(self) -> None:
        service = ToolVisibilityService(FakeToolVisibilityRepository())
        await service.set_default("a_tool", False)
        await service.set_override(10, "a_tool", True)

        await service.clear_override(10, "a_tool")

        self.assertFalse(await service.is_enabled("a_tool", guild_id=10))


if __name__ == "__main__":
    unittest.main()
