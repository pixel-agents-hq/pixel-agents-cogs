"""Smoke tests: the Components V2 panel constructs without error and its
role-select callback round-trips through the Cog's public API. Deep
UI-behavior coverage lives in the pure application/domain tests instead --
this layer is thin by design."""

from __future__ import annotations

import unittest

import discord

from ..adapters.settings_ui import SharedSettingsView, build_shared_settings_container
from ..corridor import Corridor
from ..domain import (
    GuildSettings,
    IconPreference,
    IconSource,
    PermissionSettings,
    ReplyMode,
    ReplyPreferences,
)
from .conftest import FakeBot, FakeGuild


def _settings(guild_id: int) -> GuildSettings:
    return GuildSettings(
        guild_id=guild_id,
        reply=ReplyPreferences(
            mode=ReplyMode.EMBED,
            show_timestamp=True,
            footer_text=None,
            icon=IconPreference(source=IconSource.BOT),
        ),
        permissions=PermissionSettings(
            moderator_role_ids=frozenset(), privileged_role_ids=frozenset()
        ),
    )


class TestSettingsUiConstruction(unittest.TestCase):
    def test_container_builds_without_error(self) -> None:
        container = build_shared_settings_container(_settings(1))

        self.assertTrue(container.children)

    def test_view_wraps_the_container(self) -> None:
        view = SharedSettingsView(_settings(1))

        self.assertTrue(view.children)


class TestRoleSelectCallback(unittest.IsolatedAsyncioTestCase):
    async def test_selecting_roles_persists_via_corridor_api(self) -> None:
        bot = FakeBot()
        guild = FakeGuild(guild_id=1)
        bot.register_guild(guild)
        corridor = Corridor(bot=bot)
        bot.add_cog(corridor)

        container = build_shared_settings_container(_settings(1))
        # children: [0] summary, [1] mode/timestamp/edit row, [2] icon source row,
        # [3] moderator role row, [4] privileged role row
        mod_row = container.children[3]
        role_select = mod_row.children[0]
        role_select.values = [type("R", (), {"id": 500})(), type("R", (), {"id": 600})()]

        interaction = discord.Interaction(guild=guild, client=bot)
        await role_select.callback(interaction)

        settings = await corridor.guild_settings(1)
        self.assertEqual(settings.permissions.moderator_role_ids, frozenset({500, 600}))
