"""Exercises RedCorridorRepository against the fake Config installed by the
package-root conftest.py -- the only test needing that stub directly rather
than through the full Cog."""

from __future__ import annotations

import unittest

from ..domain import IconPreference, IconSource, ReplyMode
from ..infrastructure import RedCorridorRepository


class TestRedCorridorRepository(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.repository = RedCorridorRepository.create(cog=object())

    async def test_defaults(self) -> None:
        settings = await self.repository.guild_settings(guild_id=1)

        self.assertEqual(settings.reply.mode, ReplyMode.EMBED)
        self.assertTrue(settings.reply.show_timestamp)
        self.assertIsNone(settings.reply.footer_text)
        self.assertEqual(settings.reply.icon.source, IconSource.BOT)
        self.assertEqual(settings.permissions.moderator_role_ids, frozenset())
        self.assertEqual(settings.permissions.privileged_role_ids, frozenset())

    async def test_set_reply_mode_persists(self) -> None:
        await self.repository.set_reply_mode(1, ReplyMode.TEXT)

        settings = await self.repository.guild_settings(1)

        self.assertEqual(settings.reply.mode, ReplyMode.TEXT)

    async def test_set_icon_preference_persists(self) -> None:
        await self.repository.set_icon_preference(
            1, IconPreference(source=IconSource.CUSTOM, custom_url="https://x/y.png")
        )

        settings = await self.repository.guild_settings(1)

        self.assertEqual(settings.reply.icon.source, IconSource.CUSTOM)
        self.assertEqual(settings.reply.icon.custom_url, "https://x/y.png")

    async def test_add_and_remove_moderator_role(self) -> None:
        await self.repository.add_moderator_role(1, 100)
        await self.repository.add_moderator_role(1, 200)
        settings = await self.repository.guild_settings(1)
        self.assertEqual(settings.permissions.moderator_role_ids, frozenset({100, 200}))

        await self.repository.remove_moderator_role(1, 100)
        settings = await self.repository.guild_settings(1)
        self.assertEqual(settings.permissions.moderator_role_ids, frozenset({200}))

    async def test_set_privileged_role_ids_replaces_whole_set(self) -> None:
        await self.repository.add_privileged_role(1, 999)

        await self.repository.set_privileged_role_ids(1, frozenset({300, 400}))

        settings = await self.repository.guild_settings(1)
        self.assertEqual(settings.permissions.privileged_role_ids, frozenset({300, 400}))

    async def test_settings_are_scoped_per_guild(self) -> None:
        await self.repository.add_moderator_role(1, 100)

        other_guild = await self.repository.guild_settings(2)

        self.assertEqual(other_guild.permissions.moderator_role_ids, frozenset())
