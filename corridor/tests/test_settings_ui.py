"""Smoke tests: the Components V2 panel constructs without error and its
role-select / group-management callbacks round-trip through the Cog's
public API. Deep UI-behavior coverage lives in the pure application/domain
tests instead -- this layer is thin by design."""

from __future__ import annotations

import unittest

import discord

from ..adapters.settings_ui import (
    AddGroupModal,
    RenameGroupModal,
    SharedSettingsView,
    TierLabelsModal,
    build_shared_settings_container,
)
from ..corridor import Corridor
from ..domain import (
    GuildSettings,
    IconPreference,
    IconSource,
    PermissionGroupDef,
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
            groups=(
                PermissionGroupDef(key="building_manager", label="Building Manager"),
                PermissionGroupDef(key="keyholder", label="Keyholder"),
            ),
        ),
    )


class TestSettingsUiConstruction(unittest.TestCase):
    def test_container_builds_without_error(self) -> None:
        container = build_shared_settings_container(_settings(1))

        self.assertTrue(container.children)

    def test_view_wraps_the_container(self) -> None:
        view = SharedSettingsView(_settings(1))

        self.assertTrue(view.children)

    def test_one_role_select_row_per_group(self) -> None:
        container = build_shared_settings_container(_settings(1))

        role_select_rows = [
            child
            for child in container.children
            if getattr(child, "children", None)
            and isinstance(child.children[0], discord.ui.RoleSelect)
        ]

        self.assertEqual(len(role_select_rows), 2)


class TestRoleSelectCallback(unittest.IsolatedAsyncioTestCase):
    async def test_selecting_roles_persists_via_corridor_api(self) -> None:
        bot = FakeBot()
        guild = FakeGuild(guild_id=1)
        bot.register_guild(guild)
        corridor = Corridor(bot=bot)
        bot.add_cog(corridor)

        container = build_shared_settings_container(_settings(1))
        role_select_row = next(
            child
            for child in container.children
            if getattr(child, "children", None)
            and isinstance(child.children[0], discord.ui.RoleSelect)
        )
        role_select = role_select_row.children[0]
        role_select.values = [type("R", (), {"id": 500})(), type("R", (), {"id": 600})()]

        interaction = discord.Interaction(guild=guild, client=bot)
        await role_select.callback(interaction)

        settings = await corridor.guild_settings(1)
        building_manager = settings.permissions.group("building_manager")
        assert building_manager is not None
        self.assertEqual(building_manager.role_ids, frozenset({500, 600}))


class TestGroupManagementModals(unittest.IsolatedAsyncioTestCase):
    async def test_add_group_modal_creates_group(self) -> None:
        bot = FakeBot()
        guild = FakeGuild(guild_id=1)
        bot.register_guild(guild)
        corridor = Corridor(bot=bot)
        bot.add_cog(corridor)

        modal = AddGroupModal()
        modal.key_input.value = "hr"
        modal.label_input.value = "HR"
        interaction = discord.Interaction(guild=guild, client=bot)

        await modal.on_submit(interaction)

        groups = await corridor.list_permission_groups(1)
        self.assertIn("hr", {group.key for group in groups})

    async def test_add_group_modal_rejects_reserved_key(self) -> None:
        bot = FakeBot()
        guild = FakeGuild(guild_id=1)
        bot.register_guild(guild)
        corridor = Corridor(bot=bot)
        bot.add_cog(corridor)

        modal = AddGroupModal()
        modal.key_input.value = "owner"
        modal.label_input.value = "Nope"
        interaction = discord.Interaction(guild=guild, client=bot)

        await modal.on_submit(interaction)

        groups = await corridor.list_permission_groups(1)
        self.assertNotIn("owner", {group.key for group in groups})

    async def test_rename_group_modal_updates_label_only(self) -> None:
        bot = FakeBot()
        guild = FakeGuild(guild_id=1)
        bot.register_guild(guild)
        corridor = Corridor(bot=bot)
        bot.add_cog(corridor)
        await corridor.set_group_role_ids(1, "keyholder", frozenset({42}))

        modal = RenameGroupModal("keyholder", "Keyholder")
        modal.label_input.value = "Trusted Member"
        interaction = discord.Interaction(guild=guild, client=bot)

        await modal.on_submit(interaction)

        settings = await corridor.guild_settings(1)
        keyholder = settings.permissions.group("keyholder")
        assert keyholder is not None
        self.assertEqual(keyholder.label, "Trusted Member")
        self.assertEqual(keyholder.role_ids, frozenset({42}))

    async def test_tier_labels_modal_renames_owner_and_employee(self) -> None:
        bot = FakeBot()
        guild = FakeGuild(guild_id=1)
        bot.register_guild(guild)
        corridor = Corridor(bot=bot)
        bot.add_cog(corridor)

        modal = TierLabelsModal(_settings(1))
        modal.owner_input.value = "Founder"
        modal.employee_input.value = "Resident"
        interaction = discord.Interaction(guild=guild, client=bot)

        await modal.on_submit(interaction)

        settings = await corridor.guild_settings(1)
        self.assertEqual(settings.permissions.owner_label, "Founder")
        self.assertEqual(settings.permissions.employee_label, "Resident")
