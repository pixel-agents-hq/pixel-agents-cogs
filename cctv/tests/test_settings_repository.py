"""RedCctvRepository's own validation -- the clear-delay setters must
reject not just negative values but NaN/infinity too, since neither
compares less than zero (`float("nan") < 0` and `float("inf") < 0` are
both `False`), and either would reach `asyncio.sleep` downstream in
`event_subscriptions_discord.py`/`event_subscriptions_editor.py`."""

from __future__ import annotations

import math
import unittest

from ..infrastructure.settings_repository import RedCctvRepository


class TestClearDelayValidation(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.repository = RedCctvRepository.create(cog=object())

    async def test_a_negative_discord_delay_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            await self.repository.set_discord_clear_delay(-1.0)

    async def test_nan_discord_delay_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            await self.repository.set_discord_clear_delay(math.nan)

    async def test_infinite_discord_delay_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            await self.repository.set_discord_clear_delay(math.inf)

    async def test_a_negative_editor_delay_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            await self.repository.set_editor_clear_delay(-1.0)

    async def test_nan_editor_delay_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            await self.repository.set_editor_clear_delay(math.nan)

    async def test_infinite_editor_delay_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            await self.repository.set_editor_clear_delay(math.inf)

    async def test_a_valid_delay_persists_for_both_pages(self) -> None:
        await self.repository.set_discord_clear_delay(3.5)
        await self.repository.set_editor_clear_delay(4.5)

        settings = await self.repository.global_settings()

        self.assertEqual(settings.discord_clear_delay, 3.5)
        self.assertEqual(settings.editor_clear_delay, 4.5)


if __name__ == "__main__":
    unittest.main()
