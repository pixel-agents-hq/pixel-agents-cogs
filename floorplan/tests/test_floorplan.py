"""Floorplan lifecycle and Pixelagents handoff tests."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from corridor.domain import OfficeStateKind

from floorplan.floorplan import Floorplan
from floorplan.tests.conftest import FakeCorridor


def make_cog() -> Floorplan:
    bot = MagicMock()
    bot.guilds = []
    bot.is_owner = AsyncMock(return_value=False)
    return Floorplan(bot)


class TestFloorplanLifecycle(unittest.IsolatedAsyncioTestCase):
    async def test_load_registers_only_pixel_index_tools_and_dependencies(self) -> None:
        cog = make_cog()
        corridor = FakeCorridor()
        pixelagents = MagicMock()
        cog._pixel_index_client.start = AsyncMock()

        with (
            patch(
                "floorplan.adapters.cog_base.ensure_corridor_loaded",
                new=AsyncMock(return_value=corridor),
            ),
            patch(
                "corridor.dependency_loader.ensure_loaded",
                new=AsyncMock(return_value=pixelagents),
            ),
        ):
            await cog.cog_load()

        assert cog._corridor is corridor
        assert cog._pixelagents is pixelagents
        assert corridor.registered_dependents == {"floorplan"}
        assert corridor.registered_llm_tools_calls == [(cog, "Floorplan")]
        cog._pixel_index_client.start.assert_awaited_once_with()

    async def test_unload_unregisters_and_closes_the_http_client(self) -> None:
        cog = make_cog()
        corridor = FakeCorridor()
        corridor.register_dependent("floorplan")
        cog._corridor = corridor
        cog._pixel_index_client.close = AsyncMock()

        await cog.cog_unload()

        assert corridor.registered_dependents == set()
        assert corridor.unregistered_tool_owners == ["Floorplan"]
        cog._pixel_index_client.close.assert_awaited_once_with()

    async def test_catalogue_layout_updates_the_discord_aggregate(self) -> None:
        cog = make_cog()
        cog._pixelagents = MagicMock()
        cog._pixelagents.set_office_layout = AsyncMock()
        layout = {"version": 1, "cols": 1, "rows": 1, "tiles": [1], "furniture": []}

        await cog._apply_catalogue_layout(layout)

        cog._pixelagents.set_office_layout.assert_awaited_once_with(OfficeStateKind.DISCORD, layout)

    async def test_refresh_pixelagents_replaces_the_cached_reference(self) -> None:
        # Regression test for: pixelagents reloading independently of
        # floorplan's own reload left floorplan holding a stale
        # `_pixelagents` Cog reference forever, since `ensure_loaded` only
        # resolves once, in floorplan's own `cog_load`. pixelagents now
        # pushes its fresh instance to every loaded cog exposing
        # `refresh_pixelagents` -- floorplan only reads `self._pixelagents`
        # directly, so updating the attribute is the whole fix.
        cog = make_cog()
        cog._pixelagents = MagicMock()
        fresh = MagicMock()

        await cog.refresh_pixelagents(fresh)

        assert cog._pixelagents is fresh


class TestFloorplanAuthorization(unittest.IsolatedAsyncioTestCase):
    async def test_owner_can_load_a_layout(self) -> None:
        cog = make_cog()
        cog.bot.is_owner = AsyncMock(return_value=True)

        assert await cog._can_edit_layout_user(7)

    async def test_keyholder_in_any_guild_can_load_a_layout(self) -> None:
        cog = make_cog()
        member = SimpleNamespace(id=7)
        guild = MagicMock()
        guild.get_member.return_value = member
        cog.bot.guilds = [guild]
        cog._corridor = FakeCorridor(keyholders={7})

        assert await cog._can_edit_layout_user(7)
        assert cog._corridor.capability_checks == [(7, "keyholder")]

    async def test_unknown_user_cannot_load_a_layout(self) -> None:
        cog = make_cog()
        guild = MagicMock()
        guild.get_member.return_value = None
        cog.bot.guilds = [guild]
        cog._corridor = FakeCorridor()

        assert not await cog._can_edit_layout_user(7)
        assert not await cog._can_edit_layout_user(0)


if __name__ == "__main__":
    unittest.main()
