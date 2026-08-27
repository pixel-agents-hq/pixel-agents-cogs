"""Exercises RedCorridorRepository against the fake Config installed by the
package-root conftest.py -- the only test needing that stub directly rather
than through the full Cog."""

from __future__ import annotations

import unittest

import pytest

from ..domain import IconPreference, IconSource, ReplyMode
from ..infrastructure import DEFAULT_LLM_BASE_URL, RedCorridorRepository


class TestRedCorridorRepository(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.repository = RedCorridorRepository.create(cog=object())

    async def test_defaults(self) -> None:
        settings = await self.repository.guild_settings(guild_id=1)

        self.assertEqual(settings.reply.mode, ReplyMode.EMBED)
        self.assertTrue(settings.reply.show_timestamp)
        self.assertIsNone(settings.reply.footer_text)
        self.assertEqual(settings.reply.icon.source, IconSource.BOT)
        self.assertEqual(settings.permissions.owner_label, "Owner")
        self.assertEqual(settings.permissions.employee_label, "Employee")
        self.assertEqual(
            {group.key for group in settings.permissions.groups},
            {"building_manager", "keyholder"},
        )
        self.assertTrue(all(group.role_ids == frozenset() for group in settings.permissions.groups))
        self.assertTrue(
            all(group.permission_names == frozenset() for group in settings.permissions.groups)
        )

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

    async def test_set_group_role_ids_persists(self) -> None:
        await self.repository.set_group_role_ids(1, "building_manager", frozenset({100, 200}))

        settings = await self.repository.guild_settings(1)

        building_manager = settings.permissions.group("building_manager")
        assert building_manager is not None
        self.assertEqual(building_manager.role_ids, frozenset({100, 200}))

    async def test_add_and_remove_permission_group(self) -> None:
        await self.repository.add_permission_group(
            1, "hr", "HR", frozenset({777}), frozenset({"manage_roles"})
        )

        settings = await self.repository.guild_settings(1)
        hr = settings.permissions.group("hr")
        assert hr is not None
        self.assertEqual(hr.label, "HR")
        self.assertEqual(hr.role_ids, frozenset({777}))
        self.assertEqual(hr.permission_names, frozenset({"manage_roles"}))

        await self.repository.remove_permission_group(1, "hr")
        settings = await self.repository.guild_settings(1)
        self.assertIsNone(settings.permissions.group("hr"))

    async def test_set_group_permissions_persists(self) -> None:
        await self.repository.set_group_permissions(
            1, "keyholder", frozenset({"kick_members", "ban_members"})
        )

        settings = await self.repository.guild_settings(1)

        keyholder = settings.permissions.group("keyholder")
        assert keyholder is not None
        self.assertEqual(keyholder.permission_names, frozenset({"kick_members", "ban_members"}))

    async def test_set_group_permissions_leaves_role_ids_untouched(self) -> None:
        await self.repository.set_group_role_ids(1, "keyholder", frozenset({300}))

        await self.repository.set_group_permissions(1, "keyholder", frozenset({"ban_members"}))

        settings = await self.repository.guild_settings(1)
        keyholder = settings.permissions.group("keyholder")
        assert keyholder is not None
        self.assertEqual(keyholder.role_ids, frozenset({300}))
        self.assertEqual(keyholder.permission_names, frozenset({"ban_members"}))

    async def test_set_group_role_ids_leaves_permission_names_untouched(self) -> None:
        await self.repository.set_group_permissions(1, "keyholder", frozenset({"ban_members"}))

        await self.repository.set_group_role_ids(1, "keyholder", frozenset({300}))

        settings = await self.repository.guild_settings(1)
        keyholder = settings.permissions.group("keyholder")
        assert keyholder is not None
        self.assertEqual(keyholder.role_ids, frozenset({300}))
        self.assertEqual(keyholder.permission_names, frozenset({"ban_members"}))

    async def test_add_permission_group_rejects_reserved_key(self) -> None:
        with pytest.raises(ValueError):
            await self.repository.add_permission_group(1, "owner", "Owner")

    async def test_add_permission_group_rejects_duplicate_key(self) -> None:
        with pytest.raises(ValueError):
            await self.repository.add_permission_group(1, "keyholder", "Duplicate")

    async def test_set_group_label_renames_without_changing_key_or_roles(self) -> None:
        await self.repository.set_group_role_ids(1, "keyholder", frozenset({300}))
        await self.repository.set_group_permissions(1, "keyholder", frozenset({"ban_members"}))

        await self.repository.set_group_label(1, "keyholder", "Trusted Member")

        settings = await self.repository.guild_settings(1)
        keyholder = settings.permissions.group("keyholder")
        assert keyholder is not None
        self.assertEqual(keyholder.label, "Trusted Member")
        self.assertEqual(keyholder.role_ids, frozenset({300}))
        self.assertEqual(keyholder.permission_names, frozenset({"ban_members"}))

    async def test_set_owner_and_employee_labels_persist(self) -> None:
        await self.repository.set_owner_label(1, "Founder")
        await self.repository.set_employee_label(1, "Resident")

        settings = await self.repository.guild_settings(1)

        self.assertEqual(settings.permissions.owner_label, "Founder")
        self.assertEqual(settings.permissions.employee_label, "Resident")

    async def test_settings_are_scoped_per_guild(self) -> None:
        await self.repository.set_group_role_ids(1, "building_manager", frozenset({100}))

        other_guild = await self.repository.guild_settings(2)

        building_manager = other_guild.permissions.group("building_manager")
        assert building_manager is not None
        self.assertEqual(building_manager.role_ids, frozenset())


class TestLLMSettingsRepository(unittest.IsolatedAsyncioTestCase):
    """The shared LLM connection -- global (bot-wide), not per-guild --
    moved here from pico, see docs/architect-design.md."""

    def setUp(self) -> None:
        self.repository = RedCorridorRepository.create(cog=object())

    async def test_defaults(self) -> None:
        settings = await self.repository.llm_settings()

        self.assertEqual(settings.llm_base_url, DEFAULT_LLM_BASE_URL)
        self.assertIsNone(settings.llm_api_key)
        self.assertIsNotNone(settings.llm_model)

    async def test_set_llm_base_url_persists(self) -> None:
        await self.repository.set_llm_base_url("https://example.test/")

        settings = await self.repository.llm_settings()

        self.assertEqual(settings.llm_base_url, "https://example.test/")

    async def test_set_llm_api_key_persists(self) -> None:
        await self.repository.set_llm_api_key("sk-secret")

        settings = await self.repository.llm_settings()

        self.assertEqual(settings.llm_api_key, "sk-secret")

    async def test_set_llm_model_persists(self) -> None:
        await self.repository.set_llm_model("gpt-test")

        settings = await self.repository.llm_settings()

        self.assertEqual(settings.llm_model, "gpt-test")
