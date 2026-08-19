"""Smoke tests: the Components V2 panel constructs without error and its
role-select / group-management callbacks round-trip through the Cog's
public API. Deep UI-behavior coverage lives in the pure application/domain
tests instead -- this layer is thin by design."""

from __future__ import annotations

import unittest

import discord

import ui_limits

from ..adapters.settings_ui import (
    AddGroupModal,
    RenameGroupModal,
    SharedSettingsModal,
    SharedSettingsView,
    TierLabelsModal,
    _edit_button,
    _mode_button,
    _timestamp_button,
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
from ..testing import followup_messages, refreshed_view, shown_modal
from .conftest import FakeBot, FakeGuild


def _find_component(root: object, label_prefix: str) -> object:
    """Locate a control by its rendered label prefix, walking the whole
    View/Container/ActionRow tree -- used to grab "the button as it exists
    in a freshly-refreshed view" rather than the one built earlier."""
    for node in ui_limits.iter_ui_tree(root):
        label = getattr(node, "label", None)
        if label and label.startswith(label_prefix) and hasattr(node, "callback"):
            return node
    raise AssertionError(f"no component with label prefix {label_prefix!r} found in {root!r}")


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

    def test_one_permission_select_row_per_group(self) -> None:
        container = build_shared_settings_container(_settings(1))

        permission_select_rows = [
            child
            for child in container.children
            if getattr(child, "children", None)
            and type(child.children[0]) is discord.ui.Select
            and (child.children[0].placeholder or "").endswith("Discord permissions")
        ]

        self.assertEqual(len(permission_select_rows), 2)


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


class TestPermissionSelectCallback(unittest.IsolatedAsyncioTestCase):
    async def test_selecting_permissions_persists_via_corridor_api(self) -> None:
        bot = FakeBot()
        guild = FakeGuild(guild_id=1)
        bot.register_guild(guild)
        corridor = Corridor(bot=bot)
        bot.add_cog(corridor)

        container = build_shared_settings_container(_settings(1))
        permission_select_row = next(
            child
            for child in container.children
            if getattr(child, "children", None)
            and type(child.children[0]) is discord.ui.Select
            and (child.children[0].placeholder or "").endswith("Discord permissions")
        )
        permission_select = permission_select_row.children[0]
        permission_select.values = ["kick_members", "ban_members"]

        interaction = discord.Interaction(guild=guild, client=bot)
        await permission_select.callback(interaction)

        settings = await corridor.guild_settings(1)
        building_manager = settings.permissions.group("building_manager")
        assert building_manager is not None
        self.assertEqual(
            building_manager.permission_names, frozenset({"kick_members", "ban_members"})
        )


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
        await corridor.set_group_permissions(1, "keyholder", frozenset({"ban_members"}))

        modal = RenameGroupModal("keyholder", "Keyholder")
        modal.label_input.value = "Trusted Member"
        interaction = discord.Interaction(guild=guild, client=bot)

        await modal.on_submit(interaction)

        settings = await corridor.guild_settings(1)
        keyholder = settings.permissions.group("keyholder")
        assert keyholder is not None
        self.assertEqual(keyholder.label, "Trusted Member")
        self.assertEqual(keyholder.role_ids, frozenset({42}))
        self.assertEqual(keyholder.permission_names, frozenset({"ban_members"}))

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


class TestToggleUsesFreshStateNotConstructionSnapshot(unittest.IsolatedAsyncioTestCase):
    """Regression coverage for the reported bug: toggle clicks appeared to
    do nothing because the mutation was computed from the settings snapshot
    captured when the panel was built, not the current stored value."""

    async def test_timestamp_button_flips_the_current_stored_value(self) -> None:
        bot = FakeBot()
        guild = FakeGuild(guild_id=1)
        bot.register_guild(guild)
        corridor = Corridor(bot=bot)
        bot.add_cog(corridor)

        # Button rendered while show_timestamp was True...
        button = _timestamp_button(current=True)
        # ...but the real stored value has since changed to False (another
        # admin toggled it, or an earlier click already flipped it and this
        # is a stale render of the panel).
        await corridor.set_show_timestamp(1, False)

        interaction = discord.Interaction(guild=guild, client=bot)
        await button.callback(interaction)

        settings = await corridor.guild_settings(1)
        # Correct: flips the *actual* False to True. A closure-bound
        # implementation would instead flip its stale `current=True` to
        # False, silently reapplying the old value instead of toggling.
        self.assertTrue(settings.reply.show_timestamp)

    async def test_mode_button_flips_the_current_stored_value(self) -> None:
        bot = FakeBot()
        guild = FakeGuild(guild_id=1)
        bot.register_guild(guild)
        corridor = Corridor(bot=bot)
        bot.add_cog(corridor)

        button = _mode_button(current=ReplyMode.EMBED)
        await corridor.set_reply_mode(1, ReplyMode.TEXT)

        interaction = discord.Interaction(guild=guild, client=bot)
        await button.callback(interaction)

        settings = await corridor.guild_settings(1)
        self.assertEqual(settings.reply.mode, ReplyMode.EMBED)

    async def test_edit_button_opens_modal_with_the_latest_footer_text(self) -> None:
        bot = FakeBot()
        guild = FakeGuild(guild_id=1)
        bot.register_guild(guild)
        corridor = Corridor(bot=bot)
        bot.add_cog(corridor)

        button = _edit_button()  # built before any footer text was ever set
        await corridor.set_footer_text(1, "Updated after the panel was built")

        interaction = discord.Interaction(guild=guild, client=bot)
        await button.callback(interaction)

        modal = shown_modal(interaction)
        self.assertIsInstance(modal, SharedSettingsModal)
        self.assertEqual(modal.footer_input.value, "Updated after the panel was built")


class TestSequentialTogglesRefreshEachTime(unittest.IsolatedAsyncioTestCase):
    """End-to-end version of the bug report: click the toggle, then click
    the *rendered* button in the panel it produced, repeatedly -- exactly
    what the user did with `!corridorsettings`."""

    async def test_two_clicks_in_a_row_flip_twice_and_each_refresh_shows_it(self) -> None:
        bot = FakeBot()
        guild = FakeGuild(guild_id=1)
        bot.register_guild(guild)
        corridor = Corridor(bot=bot)
        bot.add_cog(corridor)

        view = SharedSettingsView(_settings(1))
        button = _find_component(view, "Timestamp:")
        self.assertEqual(button.label, "Timestamp: on")

        first = discord.Interaction(guild=guild, client=bot)
        await button.callback(first)

        settings_after_first = await corridor.guild_settings(1)
        self.assertFalse(settings_after_first.reply.show_timestamp)
        panel_after_first = refreshed_view(first)
        self.assertIsInstance(panel_after_first, SharedSettingsView)
        button_after_first = _find_component(panel_after_first, "Timestamp:")
        self.assertEqual(button_after_first.label, "Timestamp: off")

        second = discord.Interaction(guild=guild, client=bot)
        await button_after_first.callback(second)

        settings_after_second = await corridor.guild_settings(1)
        self.assertTrue(settings_after_second.reply.show_timestamp)
        button_after_second = _find_component(refreshed_view(second), "Timestamp:")
        self.assertEqual(button_after_second.label, "Timestamp: on")

    async def test_add_group_modal_submission_refreshes_the_panel_and_confirms(self) -> None:
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

        self.assertIsInstance(refreshed_view(interaction), SharedSettingsView)
        messages = followup_messages(interaction)
        self.assertTrue(any("Added group" in str(message) for message in messages))


class TestInteractionLifecycleStub(unittest.IsolatedAsyncioTestCase):
    """The fake `discord.Interaction` should behave like the real one enough
    to catch misuse: an interaction can only be initially acknowledged
    once, and `followup`/`edit_original_response` require that ack first."""

    async def test_second_initial_response_raises_interaction_responded(self) -> None:
        interaction = discord.Interaction()
        await interaction.response.send_message("first", ephemeral=True)

        with self.assertRaises(discord.InteractionResponded):
            await interaction.response.send_message("second", ephemeral=True)

    async def test_send_modal_after_send_message_raises_interaction_responded(self) -> None:
        interaction = discord.Interaction()
        await interaction.response.send_message("first", ephemeral=True)

        with self.assertRaises(discord.InteractionResponded):
            await interaction.response.send_modal(AddGroupModal())

    async def test_edit_original_response_before_any_ack_raises(self) -> None:
        interaction = discord.Interaction()

        with self.assertRaises(RuntimeError):
            await interaction.edit_original_response(view=None)

    async def test_followup_before_any_ack_raises(self) -> None:
        interaction = discord.Interaction()

        with self.assertRaises(RuntimeError):
            await interaction.followup.send("too early", ephemeral=True)

    async def test_edit_original_response_after_defer_records_the_view(self) -> None:
        interaction = discord.Interaction()
        await interaction.response.defer()
        sentinel = object()

        await interaction.edit_original_response(view=sentinel)

        self.assertIs(refreshed_view(interaction), sentinel)

    async def test_edit_message_is_a_valid_first_response(self) -> None:
        interaction = discord.Interaction()
        sentinel = object()

        await interaction.response.edit_message(view=sentinel)

        self.assertTrue(interaction.response.is_done())
        self.assertIs(refreshed_view(interaction), sentinel)
