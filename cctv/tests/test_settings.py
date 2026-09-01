from __future__ import annotations

import unittest

from ..infrastructure.settings import RedSettingsRepository


class TestCctvSettings(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.repository = RedSettingsRepository.create(object())

    async def test_fresh_defaults_match_the_design(self) -> None:
        global_settings = await self.repository.global_settings()
        guild = await self.repository.guild_settings(42)

        self.assertEqual(global_settings.listener_host, "127.0.0.1")
        self.assertEqual(global_settings.listener_port, 3210)
        self.assertEqual(global_settings.discord_clear_delay, 2.0)
        self.assertEqual(global_settings.editor_clear_delay, 2.0)
        self.assertTrue(global_settings.broadcast_rich_presence)
        self.assertTrue(global_settings.broadcast_messages)
        self.assertFalse(guild.enabled)
        self.assertTrue(guild.include_bots)

    async def test_settings_are_independently_mutable(self) -> None:
        await self.repository.set_listener_host("localhost")
        await self.repository.set_listener_port(4321)
        await self.repository.set_clear_delay("discord", 1.5)
        await self.repository.set_clear_delay("editor", 4.0)
        await self.repository.set_broadcast_rich_presence(False)
        await self.repository.set_broadcast_messages(False)
        await self.repository.set_guild_enabled(42, True)
        await self.repository.set_guild_include_bots(42, False)

        global_settings = await self.repository.global_settings()
        guild = await self.repository.guild_settings(42)
        self.assertEqual(global_settings.listener_host, "localhost")
        self.assertEqual(global_settings.listener_port, 4321)
        self.assertEqual(global_settings.discord_clear_delay, 1.5)
        self.assertEqual(global_settings.editor_clear_delay, 4.0)
        self.assertFalse(global_settings.broadcast_rich_presence)
        self.assertFalse(global_settings.broadcast_messages)
        self.assertTrue(guild.enabled)
        self.assertFalse(guild.include_bots)

    async def test_invalid_listener_and_delay_values_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            await self.repository.set_listener_host("  ")
        with self.assertRaises(ValueError):
            await self.repository.set_listener_port(0)
        with self.assertRaises(ValueError):
            await self.repository.set_clear_delay("unknown", 1)
        with self.assertRaises(ValueError):
            await self.repository.set_clear_delay("discord", -1)


if __name__ == "__main__":
    unittest.main()
