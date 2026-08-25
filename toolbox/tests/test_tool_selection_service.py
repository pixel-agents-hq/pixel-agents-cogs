"""ToolSelectionService is fully testable without Red: a plain in-memory
fake satisfies the ToolSelectionRepository protocol, same pattern as
test_application_service.py's FakeNodeRepository."""

from __future__ import annotations

import unittest

from ..application import ToolSelectionService


class FakeToolSelectionRepository:
    def __init__(self, selected: frozenset[str] = frozenset()) -> None:
        self._selected = set(selected)

    async def list_selected(self) -> frozenset[str]:
        return frozenset(self._selected)

    async def add_selected(self, qualified_name: str) -> None:
        self._selected.add(qualified_name)

    async def remove_selected(self, qualified_name: str) -> None:
        self._selected.discard(qualified_name)


class TestToolSelectionService(unittest.IsolatedAsyncioTestCase):
    async def test_list_selected_starts_empty(self) -> None:
        service = ToolSelectionService(FakeToolSelectionRepository())

        self.assertEqual(await service.list_selected(), frozenset())

    async def test_select_adds_a_command(self) -> None:
        service = ToolSelectionService(FakeToolSelectionRepository())

        await service.select("deskutils count")

        self.assertEqual(await service.list_selected(), frozenset({"deskutils count"}))

    async def test_select_is_idempotent(self) -> None:
        service = ToolSelectionService(FakeToolSelectionRepository())

        await service.select("deskutils count")
        await service.select("deskutils count")

        self.assertEqual(await service.list_selected(), frozenset({"deskutils count"}))

    async def test_deselect_removes_a_command(self) -> None:
        repository = FakeToolSelectionRepository(frozenset({"deskutils count"}))
        service = ToolSelectionService(repository)

        await service.deselect("deskutils count")

        self.assertEqual(await service.list_selected(), frozenset())

    async def test_deselect_an_unselected_command_is_a_noop(self) -> None:
        service = ToolSelectionService(FakeToolSelectionRepository())

        await service.deselect("never selected")

        self.assertEqual(await service.list_selected(), frozenset())


if __name__ == "__main__":
    unittest.main()
