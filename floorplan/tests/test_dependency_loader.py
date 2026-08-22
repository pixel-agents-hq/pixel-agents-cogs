"""Unit tests for ensure_corridor_loaded (floorplan/dependency_loader.py).

The generic ensure_loaded/ensure_importable helpers this used to duplicate
for pixelagents moved to corridor/dependency_loader.py -- see
corridor/tests/test_dependency_loader.py for their tests. This file keeps
only corridor's own bootstrap loader, which can't be generalized: you cannot
import a helper from corridor before corridor is loaded.

Stubs for discord / redbot / aiohttp are installed by conftest.py.
"""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock

from redbot.core.errors import CogLoadError

from floorplan.dependency_loader import ensure_corridor_loaded


def _bot(*, already_loaded=None, spec=None, load_extension=None, after_load=None):
    bot = MagicMock()
    bot.get_cog = MagicMock(side_effect=[already_loaded, after_load])
    bot._cog_mgr.find_cog = AsyncMock(return_value=spec)
    bot.load_extension = load_extension or AsyncMock()
    bot.add_loaded_package = AsyncMock()
    return bot


class TestEnsureCorridorLoaded(unittest.IsolatedAsyncioTestCase):
    async def test_returns_already_loaded_corridor_without_loading(self):
        corridor = MagicMock()
        bot = _bot(already_loaded=corridor)

        result = await ensure_corridor_loaded(bot)

        self.assertIs(result, corridor)
        bot._cog_mgr.find_cog.assert_not_called()
        bot.load_extension.assert_not_called()

    async def test_loads_corridor_when_not_yet_loaded(self):
        corridor = MagicMock()
        spec = MagicMock()
        bot = _bot(already_loaded=None, spec=spec, after_load=corridor)

        result = await ensure_corridor_loaded(bot)

        self.assertIs(result, corridor)
        bot._cog_mgr.find_cog.assert_awaited_once_with("corridor")
        bot.load_extension.assert_awaited_once_with(spec)
        bot.add_loaded_package.assert_awaited_once_with("corridor")

    async def test_missing_corridor_reports_a_user_facing_load_error(self):
        bot = _bot(already_loaded=None, spec=None)

        with self.assertRaisesRegex(CogLoadError, "not installed"):
            await ensure_corridor_loaded(bot)

        bot.load_extension.assert_not_called()


if __name__ == "__main__":
    unittest.main()
