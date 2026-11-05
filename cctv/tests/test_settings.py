from __future__ import annotations

import unittest

from ..infrastructure.settings import RedSettingsRepository, validate_public_base_url


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
        self.assertIsNone(global_settings.public_base_url)
        self.assertFalse(guild.enabled)
        self.assertTrue(guild.broadcast_rich_presence)
        self.assertTrue(guild.broadcast_messages)
        self.assertTrue(guild.include_bots)
        self.assertFalse(guild.show_offline)
        self.assertTrue(guild.ghost_offline)
        self.assertTrue(guild.ghost_bots)

    async def test_settings_are_independently_mutable(self) -> None:
        await self.repository.set_listener_host("localhost")
        await self.repository.set_listener_port(4321)
        await self.repository.set_clear_delay("discord", 1.5)
        await self.repository.set_clear_delay("editor", 4.0)
        await self.repository.set_public_base_url("https://dash.example.com/")
        await self.repository.set_guild_broadcast_rich_presence(42, False)
        await self.repository.set_guild_broadcast_messages(42, False)
        await self.repository.set_guild_enabled(42, True)
        await self.repository.set_guild_include_bots(42, False)
        await self.repository.set_guild_show_offline(42, True)
        await self.repository.set_guild_ghost_offline(42, False)
        await self.repository.set_guild_ghost_bots(42, False)

        global_settings = await self.repository.global_settings()
        guild = await self.repository.guild_settings(42)
        self.assertEqual(global_settings.listener_host, "localhost")
        self.assertEqual(global_settings.listener_port, 4321)
        self.assertEqual(global_settings.discord_clear_delay, 1.5)
        self.assertEqual(global_settings.editor_clear_delay, 4.0)
        self.assertEqual(global_settings.public_base_url, "https://dash.example.com")
        self.assertFalse(guild.broadcast_rich_presence)
        self.assertFalse(guild.broadcast_messages)
        self.assertTrue((await self.repository.guild_settings(43)).broadcast_messages)
        self.assertTrue(guild.enabled)
        self.assertFalse(guild.include_bots)
        self.assertTrue(guild.show_offline)
        self.assertFalse(guild.ghost_offline)
        self.assertFalse(guild.ghost_bots)

    async def test_invalid_listener_and_delay_values_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            await self.repository.set_listener_host("  ")
        with self.assertRaises(ValueError):
            await self.repository.set_listener_port(0)
        with self.assertRaises(ValueError):
            await self.repository.set_clear_delay("unknown", 1)
        with self.assertRaises(ValueError):
            await self.repository.set_clear_delay("discord", -1)


class TestPublicBaseUrl(unittest.TestCase):
    def test_accepts_an_https_origin(self) -> None:
        for raw, clean in (
            ("https://dash.example.com", "https://dash.example.com"),
            (" <https://dash.example.com/> ", "https://dash.example.com"),
            ("https://dash.example.com:8443", "https://dash.example.com:8443"),
            ("http://localhost:42356", "http://localhost:42356"),
            ("http://127.0.0.1", "http://127.0.0.1"),
        ):
            with self.subTest(raw=raw):
                self.assertEqual(validate_public_base_url(raw), clean)

    def test_rejects_anything_but_a_bare_origin(self) -> None:
        for raw in (
            "dash.example.com",
            "ftp://dash.example.com",
            "http://dash.example.com",
            "https://dash.example.com/third-party",
            "https://dash.example.com?x=1",
            "https://dash.example.com#x",
            "https://user@dash.example.com",
            "https://",
            "https://dash.example.com:99999",
            "https://dash example.com",
            "javascript:alert(1)",
        ):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                validate_public_base_url(raw)


if __name__ == "__main__":
    unittest.main()
