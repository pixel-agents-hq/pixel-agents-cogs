"""RedToolSelectionRepository against the redbot Config stub -- needs the
package-root conftest.py's stubs installed, same reason as
test_cog_commands.py."""

from __future__ import annotations

import unittest

from ..infrastructure import RedToolSelectionRepository


class TestRedToolSelectionRepository(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.repository = RedToolSelectionRepository.create(object())

    async def test_starts_empty(self) -> None:
        self.assertEqual(await self.repository.list_selected(), frozenset())

    async def test_add_selected_persists_it(self) -> None:
        await self.repository.add_selected("deskutils count")

        self.assertEqual(await self.repository.list_selected(), frozenset({"deskutils count"}))

    async def test_add_selected_is_idempotent(self) -> None:
        await self.repository.add_selected("deskutils count")
        await self.repository.add_selected("deskutils count")

        self.assertEqual(await self.repository.list_selected(), frozenset({"deskutils count"}))

    async def test_remove_selected_drops_it(self) -> None:
        await self.repository.add_selected("deskutils count")

        await self.repository.remove_selected("deskutils count")

        self.assertEqual(await self.repository.list_selected(), frozenset())

    async def test_remove_selected_an_unselected_command_is_a_noop(self) -> None:
        await self.repository.remove_selected("never selected")

        self.assertEqual(await self.repository.list_selected(), frozenset())

    async def test_multiple_selections_round_trip(self) -> None:
        await self.repository.add_selected("deskutils count")
        await self.repository.add_selected("toolbox greet")

        self.assertEqual(
            await self.repository.list_selected(),
            frozenset({"deskutils count", "toolbox greet"}),
        )


if __name__ == "__main__":
    unittest.main()
