"""Smoke test: the composed Cog constructs and loads/unloads cleanly."""

from __future__ import annotations

import unittest

from ..cctv import Cctv
from .conftest import FakeBot, FakeCorridor, FakePixelAgents


class TestSmoke(unittest.IsolatedAsyncioTestCase):
    async def test_cog_load_and_unload(self) -> None:
        corridor = FakeCorridor()
        pixelagents = FakePixelAgents(corridor=corridor)
        bot = FakeBot(corridor=corridor, pixelagents=pixelagents)
        cog = Cctv(bot=bot)

        await cog.cog_load()
        await cog.cog_unload()


if __name__ == "__main__":
    unittest.main()
