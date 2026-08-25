"""RedToolVisibilityRepository against the redbot Config stub -- needs the
package-root conftest.py's stubs installed, same reason as
test_tool_selection_repository.py."""

from __future__ import annotations

import unittest

from ..infrastructure import RedToolVisibilityRepository


class TestRedToolVisibilityRepository(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.repository = RedToolVisibilityRepository.create(object())

    async def test_default_starts_unset(self) -> None:
        self.assertIsNone(await self.repository.get_default("a_tool"))

    async def test_set_default_persists_it(self) -> None:
        await self.repository.set_default("a_tool", False)

        self.assertFalse(await self.repository.get_default("a_tool"))

    async def test_set_default_does_not_clobber_a_different_tools_default(self) -> None:
        await self.repository.set_default("a_tool", False)
        await self.repository.set_default("b_tool", True)

        self.assertFalse(await self.repository.get_default("a_tool"))
        self.assertTrue(await self.repository.get_default("b_tool"))

    async def test_override_starts_unset(self) -> None:
        self.assertIsNone(await self.repository.get_override(10, "a_tool"))

    async def test_set_override_persists_it_for_that_guild_only(self) -> None:
        await self.repository.set_override(10, "a_tool", True)

        self.assertTrue(await self.repository.get_override(10, "a_tool"))
        self.assertIsNone(await self.repository.get_override(20, "a_tool"))

    async def test_clear_override_removes_it(self) -> None:
        await self.repository.set_override(10, "a_tool", True)

        await self.repository.clear_override(10, "a_tool")

        self.assertIsNone(await self.repository.get_override(10, "a_tool"))

    async def test_clear_override_an_unset_one_is_a_noop(self) -> None:
        await self.repository.clear_override(10, "a_tool")

        self.assertIsNone(await self.repository.get_override(10, "a_tool"))


if __name__ == "__main__":
    unittest.main()
