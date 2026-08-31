from __future__ import annotations

import unittest

from ..domain import OfficeState, OfficeStateKind
from ..infrastructure import RedOfficeStateRepository


class TestRedOfficeStateRepository(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.repository = RedOfficeStateRepository.create(cog=object())

    async def test_both_aggregates_start_absent(self) -> None:
        self.assertIsNone(await self.repository.state(OfficeStateKind.DISCORD))
        self.assertIsNone(await self.repository.state(OfficeStateKind.EDITOR))

    async def test_aggregates_are_stored_independently(self) -> None:
        discord = OfficeState(
            kind=OfficeStateKind.DISCORD,
            layout={"version": 1},
            seats={"42": {"seatId": "desk-a"}},
            revision=2,
        )
        editor = OfficeState(
            kind=OfficeStateKind.EDITOR,
            layout={"version": 2},
            seats={"agent": {"palette": 3}},
            revision=7,
        )

        await self.repository.save(discord)
        await self.repository.save(editor)

        self.assertEqual(await self.repository.state(OfficeStateKind.DISCORD), discord)
        self.assertEqual(await self.repository.state(OfficeStateKind.EDITOR), editor)

    async def test_reads_and_writes_are_defensive_copies(self) -> None:
        layout = {"version": 1, "objects": [{"id": "desk-a"}]}
        state = OfficeState(
            kind=OfficeStateKind.DISCORD,
            layout=layout,
            seats={},
            revision=1,
        )

        await self.repository.save(state)
        layout["objects"][0]["id"] = "changed"
        loaded = await self.repository.state(OfficeStateKind.DISCORD)
        assert loaded is not None
        loaded.layout["objects"][0]["id"] = "also-changed"

        reloaded = await self.repository.state(OfficeStateKind.DISCORD)
        assert reloaded is not None
        self.assertEqual(reloaded.layout["objects"][0]["id"], "desk-a")


if __name__ == "__main__":
    unittest.main()
