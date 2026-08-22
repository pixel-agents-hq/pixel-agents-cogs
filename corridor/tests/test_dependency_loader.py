"""Unit tests for ensure_loaded / ensure_importable / LazyDependency
(corridor/dependency_loader.py).

Stubs for discord / redbot / aiohttp are installed by conftest.py.
"""

from __future__ import annotations

import asyncio
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from redbot.core.errors import CogLoadError

from corridor.dependency_loader import LazyDependency, ensure_importable, ensure_loaded


def _bot(*, already_loaded=None, spec=None, load_extension=None, after_load=None):
    bot = MagicMock()
    bot.get_cog = MagicMock(side_effect=[already_loaded, after_load])
    bot._cog_mgr.find_cog = AsyncMock(return_value=spec)
    bot.load_extension = load_extension or AsyncMock()
    bot.add_loaded_package = AsyncMock()
    return bot


class TestEnsureLoaded(unittest.IsolatedAsyncioTestCase):
    async def test_returns_already_loaded_cog_without_loading(self):
        pixelagents = MagicMock()
        bot = _bot(already_loaded=pixelagents)

        result = await ensure_loaded(bot, "pixelagents", "PixelAgents")

        self.assertIs(result, pixelagents)
        bot._cog_mgr.find_cog.assert_not_called()
        bot.load_extension.assert_not_called()

    async def test_loads_the_cog_when_not_yet_loaded(self):
        pixelagents = MagicMock()
        spec = MagicMock()
        bot = _bot(already_loaded=None, spec=spec, after_load=pixelagents)

        result = await ensure_loaded(bot, "pixelagents", "PixelAgents")

        self.assertIs(result, pixelagents)
        bot._cog_mgr.find_cog.assert_awaited_once_with("pixelagents")
        bot.load_extension.assert_awaited_once_with(spec)
        bot.add_loaded_package.assert_awaited_once_with("pixelagents")

    async def test_missing_cog_reports_a_user_facing_load_error(self):
        bot = _bot(already_loaded=None, spec=None)

        with self.assertRaisesRegex(CogLoadError, "not installed"):
            await ensure_loaded(bot, "pixelagents", "PixelAgents")

        bot.load_extension.assert_not_called()

    async def test_load_extension_failure_reports_a_user_facing_load_error(self):
        spec = MagicMock()
        bot = _bot(already_loaded=None, spec=spec)
        bot.load_extension = AsyncMock(side_effect=RuntimeError("boom"))

        with self.assertRaisesRegex(CogLoadError, "could not be auto-loaded"):
            await ensure_loaded(bot, "pixelagents", "PixelAgents")

    async def test_load_without_registering_cog_reports_a_user_facing_load_error(self):
        spec = MagicMock()
        bot = _bot(already_loaded=None, spec=spec, after_load=None)

        with self.assertRaisesRegex(CogLoadError, "without registering"):
            await ensure_loaded(bot, "pixelagents", "PixelAgents")

        bot.add_loaded_package.assert_not_called()


class TestEnsureImportable(unittest.IsolatedAsyncioTestCase):
    """Must populate sys.modules (via spec.loader.load_module(), exactly what
    Red's own Bot.load_extension does internally for that part) without ever
    calling bot.load_extension/add_cog -- doing the latter is what breaks a
    later, independent load of the same package by a Downloader RPC smoke
    test that tests the dependency after the caller (see this repo's
    floorplan -> pixelagents usage for the incident this guards against)."""

    def setUp(self) -> None:
        sys.modules.pop("some_dependency", None)

    async def test_short_circuits_when_already_in_sys_modules(self) -> None:
        sys.modules["some_dependency"] = MagicMock()
        bot = MagicMock()
        bot._cog_mgr.find_cog = AsyncMock()

        await ensure_importable(bot, "some_dependency")

        bot._cog_mgr.find_cog.assert_not_called()

    async def test_loads_the_module_spec_without_touching_bot_loading(self) -> None:
        spec = MagicMock()
        bot = MagicMock()
        bot._cog_mgr.find_cog = AsyncMock(return_value=spec)
        bot.load_extension = AsyncMock()
        bot.add_cog = AsyncMock()

        await ensure_importable(bot, "some_dependency")

        bot._cog_mgr.find_cog.assert_awaited_once_with("some_dependency")
        spec.loader.load_module.assert_called_once_with()
        bot.load_extension.assert_not_awaited()
        bot.add_cog.assert_not_awaited()

    async def test_missing_package_reports_a_user_facing_load_error(self) -> None:
        bot = MagicMock()
        bot._cog_mgr.find_cog = AsyncMock(return_value=None)

        with self.assertRaisesRegex(CogLoadError, "not installed"):
            await ensure_importable(bot, "some_dependency")

    async def test_load_module_failure_reports_a_user_facing_load_error(self) -> None:
        spec = MagicMock()
        spec.loader.load_module.side_effect = RuntimeError("boom")
        bot = MagicMock()
        bot._cog_mgr.find_cog = AsyncMock(return_value=spec)

        with self.assertRaisesRegex(CogLoadError, "could not be imported"):
            await ensure_importable(bot, "some_dependency")


class TestLazyDependency(unittest.IsolatedAsyncioTestCase):
    async def test_resolve_loads_once_and_caches(self) -> None:
        pixelagents = MagicMock()
        with patch(
            "corridor.dependency_loader.ensure_loaded",
            new=AsyncMock(return_value=pixelagents),
        ) as ensure_loaded:
            dependency = LazyDependency(MagicMock(), "pixelagents", "PixelAgents")

            first = await dependency.resolve()
            second = await dependency.resolve()

        self.assertIs(first, pixelagents)
        self.assertIs(second, pixelagents)
        ensure_loaded.assert_awaited_once()

    async def test_value_reflects_none_before_and_the_cog_after_resolve(self) -> None:
        pixelagents = MagicMock()
        with patch(
            "corridor.dependency_loader.ensure_loaded",
            new=AsyncMock(return_value=pixelagents),
        ):
            dependency = LazyDependency(MagicMock(), "pixelagents", "PixelAgents")
            self.assertIsNone(dependency.value)

            await dependency.resolve()

        self.assertIs(dependency.value, pixelagents)

    async def test_concurrent_resolve_calls_race_to_a_single_load(self) -> None:
        pixelagents = MagicMock()

        async def slow_ensure_loaded(*args, **kwargs):
            await asyncio.sleep(0)
            return pixelagents

        with patch(
            "corridor.dependency_loader.ensure_loaded",
            new=AsyncMock(side_effect=slow_ensure_loaded),
        ) as ensure_loaded:
            dependency = LazyDependency(MagicMock(), "pixelagents", "PixelAgents")

            first, second = await asyncio.gather(dependency.resolve(), dependency.resolve())

        self.assertIs(first, pixelagents)
        self.assertIs(second, pixelagents)
        ensure_loaded.assert_awaited_once()

    async def test_eager_load_in_background_resolves(self) -> None:
        pixelagents = MagicMock()
        with patch(
            "corridor.dependency_loader.ensure_loaded",
            new=AsyncMock(return_value=pixelagents),
        ):
            dependency = LazyDependency(MagicMock(), "pixelagents", "PixelAgents")

            await dependency.eager_load_in_background()

        self.assertIs(dependency.value, pixelagents)

    async def test_eager_load_in_background_logs_and_swallows_a_failure(self) -> None:
        with patch(
            "corridor.dependency_loader.ensure_loaded",
            new=AsyncMock(side_effect=CogLoadError("boom")),
        ):
            logger = MagicMock()
            dependency = LazyDependency(MagicMock(), "pixelagents", "PixelAgents", logger=logger)

            await dependency.eager_load_in_background()  # must not raise

        self.assertIsNone(dependency.value)
        logger.exception.assert_called_once()


if __name__ == "__main__":
    unittest.main()
