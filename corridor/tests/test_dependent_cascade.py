"""Unloading corridor must cascade to every cog that registered itself as
depending on it -- otherwise a dependent cog keeps running with a stale
corridor reference instead of failing loudly."""

from __future__ import annotations

import unittest

from ..corridor import Corridor
from .conftest import FakeBot


class TestDependentCascade(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.bot = FakeBot()
        self.corridor = Corridor(bot=self.bot)

    async def test_cog_unload_unloads_every_registered_dependent(self) -> None:
        self.corridor.register_dependent("my_cog")
        self.corridor.register_dependent("another_cog")

        await self.corridor.cog_unload()

        self.assertCountEqual(self.bot.unload_extension_calls, ["my_cog", "another_cog"])

    async def test_unregistered_dependents_are_left_alone(self) -> None:
        self.corridor.register_dependent("my_cog")
        self.corridor.unregister_dependent("my_cog")

        await self.corridor.cog_unload()

        self.assertEqual(self.bot.unload_extension_calls, [])

    async def test_one_dependent_failing_does_not_block_the_others(self) -> None:
        self.corridor.register_dependent("broken_cog")
        self.corridor.register_dependent("healthy_cog")
        self.bot.unload_extension_failures.add("broken_cog")

        await self.corridor.cog_unload()  # must not raise

        self.assertCountEqual(self.bot.unload_extension_calls, ["broken_cog", "healthy_cog"])

    async def test_dependents_are_cleared_after_unload(self) -> None:
        self.corridor.register_dependent("my_cog")

        await self.corridor.cog_unload()
        self.bot.unload_extension_calls.clear()
        await self.corridor.cog_unload()

        self.assertEqual(self.bot.unload_extension_calls, [])
