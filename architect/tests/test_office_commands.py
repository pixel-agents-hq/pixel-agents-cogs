"""`[p]architect office ...` Discord commands -- calls the same
OfficeLayoutService the LLM tools use (test_office_tools.py). Owner-gating
follows test_cog_commands.py's own introspection convention."""

from __future__ import annotations

import unittest

from corridor.domain import OfficeStateKind

from ..architect import Architect
from .conftest import FakeBot, FakeContext, FakePixelAgents


def _descriptions(bot: FakeBot) -> list[str | None]:
    assert bot.corridor is not None
    return [reply["description"] for reply in bot.corridor.replies]


_MANIFEST = {
    "styles": [
        {
            "style": "desk",
            "kind": "desk",
            "label": "Desk",
            "facings": {
                "south": {
                    "catalog_id": "DESK_FRONT",
                    "footprint_width": 3,
                    "footprint_height": 2,
                    "background_tiles": 1,
                }
            },
            "default_facing": "south",
            "can_place_on_walls": False,
            "can_place_on_surfaces": False,
        }
    ]
}

_EMPTY_LAYOUT = {"version": 1, "cols": 5, "rows": 5, "tiles": [1] * 25, "furniture": []}


class TestOfficeCommandsAreOwnerGated(unittest.TestCase):
    def setUp(self) -> None:
        self.cog = Architect(bot=FakeBot())

    def test_office_group_is_owner_gated(self) -> None:
        self.assertTrue(getattr(self.cog.office_group.callback, "__is_owner__", False))

    def test_office_describe_is_owner_gated(self) -> None:
        self.assertTrue(getattr(self.cog.office_describe.callback, "__is_owner__", False))

    def test_office_place_is_owner_gated(self) -> None:
        self.assertTrue(getattr(self.cog.office_place_furniture.callback, "__is_owner__", False))

    def test_office_painttiles_is_owner_gated(self) -> None:
        self.assertTrue(getattr(self.cog.office_paint_tiles.callback, "__is_owner__", False))

    def test_office_createzone_is_owner_gated(self) -> None:
        self.assertTrue(getattr(self.cog.office_create_zone.callback, "__is_owner__", False))

    def test_office_resizezone_is_owner_gated(self) -> None:
        self.assertTrue(getattr(self.cog.office_resize_zone.callback, "__is_owner__", False))

    def test_office_removezone_is_owner_gated(self) -> None:
        self.assertTrue(getattr(self.cog.office_remove_zone.callback, "__is_owner__", False))

    def test_office_describetiles_is_owner_gated(self) -> None:
        self.assertTrue(getattr(self.cog.office_describe_tiles.callback, "__is_owner__", False))


class TestOfficeCommandsFunctional(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.bot = FakeBot(
            pixelagents=FakePixelAgents(
                furniture_styles=_MANIFEST,
                editor_layout=dict(_EMPTY_LAYOUT),
            )
        )
        self.cog = Architect(bot=self.bot)
        self.ctx = FakeContext()

    async def asyncSetUp(self) -> None:
        await self.cog.cog_load()

    async def test_describe_reports_an_empty_office(self) -> None:
        await self.cog.office_describe.callback(self.cog, self.ctx)

        self.assertIn("0 zone(s)", _descriptions(self.bot)[-1] or "")

    async def test_place_then_move_then_remove_furniture(self) -> None:
        await self.cog.office_place_furniture.callback(self.cog, self.ctx, "desk", "desk", 0, 0)

        self.assertIn("Placed", _descriptions(self.bot)[-1] or "")
        found = await self.cog._office_layout_service.find_furniture()
        self.assertEqual(len(found), 1)
        furniture_id = found[0].id

        await self.cog.office_move_furniture.callback(self.cog, self.ctx, furniture_id, 1, 1)
        self.assertIn("Moved", _descriptions(self.bot)[-1] or "")

        await self.cog.office_remove_furniture.callback(self.cog, self.ctx, furniture_id)
        self.assertIn("Removed", _descriptions(self.bot)[-1] or "")
        self.assertEqual(await self.cog._office_layout_service.find_furniture(), [])

    async def test_place_furniture_with_unknown_kind_reports_error(self) -> None:
        await self.cog.office_place_furniture.callback(
            self.cog, self.ctx, "not_a_real_kind", "desk", 0, 0
        )

        self.assertIn("Unknown kind", _descriptions(self.bot)[-1] or "")

    async def test_remove_unknown_furniture_reports_error(self) -> None:
        await self.cog.office_remove_furniture.callback(self.cog, self.ctx, "nonexistent")

        message = _descriptions(self.bot)[-1] or ""
        self.assertIn("does not exist", message)

    async def test_mutation_advances_the_editor_aggregate(self) -> None:
        await self.cog.office_place_furniture.callback(self.cog, self.ctx, "desk", "desk", 0, 0)

        state = await self.cog._pixelagents.office_state(OfficeStateKind.EDITOR)
        self.assertEqual(state.revision, 2)
        self.assertEqual(state.layout["cols"], 5)

    async def test_painttiles_floor_then_describetiles_shows_it(self) -> None:
        await self.cog.office_paint_tiles.callback(self.cog, self.ctx, 0, 0, 1, 1, "floor", 3)

        message = _descriptions(self.bot)[-1] or ""
        self.assertIn("Painted", message)

        await self.cog.office_describe_tiles.callback(self.cog, self.ctx, 0, 0, 1, 1)
        self.assertIn("material=3", _descriptions(self.bot)[-1] or "")

    async def test_painttiles_unknown_kind_reports_error(self) -> None:
        await self.cog.office_paint_tiles.callback(self.cog, self.ctx, 0, 0, 1, 1, "not_a_kind")

        self.assertIn("Unknown kind", _descriptions(self.bot)[-1] or "")

    async def test_createzone_resizezone_removezone(self) -> None:
        await self.cog.office_create_zone.callback(
            self.cog, self.ctx, "Quiet", "cool_blue", 0, 0, 2, 2
        )
        office = await self.cog._office_layout_service.describe()
        zone_id = office.zones[0].id

        await self.cog.office_resize_zone.callback(self.cog, self.ctx, zone_id, 2, 2, 2, 2)
        self.assertIn("Resized zone", _descriptions(self.bot)[-1] or "")

        await self.cog.office_remove_zone.callback(self.cog, self.ctx, zone_id)
        self.assertIn("Removed zone", _descriptions(self.bot)[-1] or "")
        office = await self.cog._office_layout_service.describe()
        self.assertEqual(office.zones, [])
