"""Focused tests for the Components V2 Pixel Agents settings panel."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import discord

from pixelagents.adapters.settings_panel import (
    SettingsPanelView,
    SettingsRuntimeSnapshot,
    SettingsValueModal,
)
from pixelagents.application import SettingsService
from pixelagents.domain import GlobalSettings, GuildSettings
from pixelagents.pixelagents import pixelagents as PixelAgentsCog


def global_settings(**changes: object) -> GlobalSettings:
    values: dict[str, object] = {
        "ws_host": "0.0.0.0",
        "ws_port": 3210,
        "message_tool_clear_delay": 2.0,
        "broadcast_rich_presence": True,
        "broadcast_messages": False,
        "pixel_index_api_url": "https://api.example.test",
        "pixel_index_web_url": "https://web.example.test",
    }
    values.update(changes)
    return GlobalSettings(**values)  # type: ignore[arg-type]


def guild_settings(**changes: object) -> GuildSettings:
    values: dict[str, object] = {"guild_id": 42, "enabled": False, "include_bots": True}
    values.update(changes)
    return GuildSettings(**values)  # type: ignore[arg-type]


def settings_service(
    *,
    global_value: GlobalSettings | None = None,
    guild_value: GuildSettings | None = None,
) -> MagicMock:
    service = MagicMock(spec=SettingsService)
    service.global_settings = AsyncMock(return_value=global_value or global_settings())
    service.guild_settings = AsyncMock(return_value=guild_value or guild_settings())
    service.set_ws_port = AsyncMock()
    service.set_message_tool_clear_delay = AsyncMock()
    service.set_broadcast_rich_presence = AsyncMock()
    service.set_broadcast_messages = AsyncMock()
    service.enable_guild = AsyncMock(return_value="Sync complete.")
    service.disable_guild = AsyncMock()
    service.set_include_bots = AsyncMock(return_value=None)
    service.set_pixel_index_api_url = AsyncMock(return_value="https://api.example.test")
    service.set_pixel_index_web_url = AsyncMock(return_value="https://web.example.test")
    return service


def interaction(*, user_id: int = 7) -> MagicMock:
    value = MagicMock()
    value.user = SimpleNamespace(id=user_id)
    value.response.is_done = MagicMock(return_value=False)
    value.response.send_message = AsyncMock()
    value.response.edit_message = AsyncMock()
    value.response.send_modal = AsyncMock()
    value.response.defer = AsyncMock()
    value.followup.send = AsyncMock()
    value.edit_original_response = AsyncMock()
    return value


class TestSettingsPanelRendering(unittest.IsolatedAsyncioTestCase):
    async def test_panel_displays_every_setting_and_runtime_value(self) -> None:
        captured: list[str] = []

        def text_display(content: str) -> MagicMock:
            captured.append(content)
            return MagicMock(content=content)

        runtime = SettingsRuntimeSnapshot(
            serving=True,
            client_count=3,
            editor_count=2,
            assets_loaded=True,
            tracked_agents=11,
        )
        with patch.object(discord.ui, "TextDisplay", side_effect=text_display):
            view = await SettingsPanelView.create(
                settings_service(),
                owner_id=7,
                guild_id=42,
                runtime_snapshot=lambda _guild_id: runtime,
            )

        content = "\n".join(captured)
        for expected in (
            "0.0.0.0",
            "3210",
            "2.0",
            "Rich presence: Enabled",
            "Messages: Disabled",
            "Presence mirroring: Disabled",
            "Include bots: Enabled",
            "https://api.example.test",
            "https://web.example.test",
            "Clients: 3 (2 editors)",
            "Tracked here: 11",
            "reload/rebind",
        ):
            self.assertIn(expected, content)
        self.assertEqual(view.timeout, 180)
        self.assertFalse(hasattr(view, "cog"))

    async def test_only_the_invoking_admin_can_interact(self) -> None:
        view = await SettingsPanelView.create(settings_service(), owner_id=7, guild_id=42)
        foreign = interaction(user_id=8)

        self.assertFalse(await view.interaction_check(foreign))
        foreign.response.send_message.assert_awaited_once_with(
            "Only the administrator who opened this panel can use its controls.",
            ephemeral=True,
        )

    async def test_timed_out_panel_is_stale(self) -> None:
        view = await SettingsPanelView.create(settings_service(), owner_id=7, guild_id=42)
        await view.on_timeout()
        old_action = interaction()

        self.assertFalse(await view.interaction_check(old_action))
        self.assertTrue(view._stale)


class TestSettingsPanelMutations(unittest.IsolatedAsyncioTestCase):
    async def make_view(
        self,
        *,
        global_value: GlobalSettings | None = None,
        guild_value: GuildSettings | None = None,
    ) -> tuple[SettingsPanelView, MagicMock]:
        service = settings_service(global_value=global_value, guild_value=guild_value)
        view = await SettingsPanelView.create(service, owner_id=7, guild_id=42)
        return view, service

    async def submit_modal(
        self,
        view: SettingsPanelView,
        show_modal: str,
        raw_value: str,
    ) -> MagicMock:
        opener = interaction()
        await getattr(view, show_modal)(opener)
        modal = opener.response.send_modal.await_args.args[0]
        self.assertIsInstance(modal, SettingsValueModal)
        modal.value_input.value = raw_value
        submission = interaction()
        await modal.on_submit(submission)
        return submission

    async def test_numeric_and_url_modals_delegate_every_value_once(self) -> None:
        pathways = (
            ("_show_port_modal", "4300", "set_ws_port", 4300),
            ("_show_delay_modal", "4.5", "set_message_tool_clear_delay", 4.5),
            (
                "_show_api_url_modal",
                "https://new-api.example.test/",
                "set_pixel_index_api_url",
                "https://new-api.example.test/",
            ),
            (
                "_show_web_url_modal",
                "https://new-web.example.test/",
                "set_pixel_index_web_url",
                "https://new-web.example.test/",
            ),
        )
        for show_modal, raw_value, method_name, expected in pathways:
            with self.subTest(setting=method_name):
                view, service = await self.make_view()
                submission = await self.submit_modal(view, show_modal, raw_value)

                getattr(service, method_name).assert_awaited_once_with(expected)
                submission.response.edit_message.assert_awaited_once()
                self.assertTrue(view._stale)

    async def test_global_boolean_buttons_delegate_once(self) -> None:
        view, service = await self.make_view()

        await view._toggle_rich_presence(interaction())

        service.set_broadcast_rich_presence.assert_awaited_once_with(False)

        view, service = await self.make_view()
        await view._toggle_messages(interaction())

        service.set_broadcast_messages.assert_awaited_once_with(True)

    async def test_guild_enable_defers_refreshes_and_reports_sync(self) -> None:
        view, service = await self.make_view(guild_value=guild_settings(enabled=False))
        action = interaction()
        action.response.is_done.return_value = True

        await view._toggle_guild(action)

        action.response.defer.assert_awaited_once_with(ephemeral=True)
        service.enable_guild.assert_awaited_once_with(42)
        action.edit_original_response.assert_awaited_once()
        action.followup.send.assert_awaited_once_with("Sync complete.", ephemeral=True)

    async def test_guild_disable_defers_and_delegates_once(self) -> None:
        view, service = await self.make_view(guild_value=guild_settings(enabled=True))
        action = interaction()
        action.response.is_done.return_value = True

        await view._toggle_guild(action)

        service.disable_guild.assert_awaited_once_with(42)
        action.response.defer.assert_awaited_once_with(ephemeral=True)
        action.edit_original_response.assert_awaited_once()

    async def test_include_bots_defers_only_when_enabled_and_delegates_once(self) -> None:
        for enabled in (False, True):
            with self.subTest(guild_enabled=enabled):
                view, service = await self.make_view(
                    guild_value=guild_settings(enabled=enabled, include_bots=True)
                )
                action = interaction()
                action.response.is_done.return_value = enabled

                await view._toggle_bots(action)

                service.set_include_bots.assert_awaited_once_with(42, False)
                self.assertEqual(action.response.defer.await_count, int(enabled))

    async def test_validation_errors_are_ephemeral_and_do_not_mutate(self) -> None:
        invalid_values = (
            ("_show_port_modal", "not-a-port", "set_ws_port"),
            ("_show_port_modal", "65536", "set_ws_port"),
            ("_show_delay_modal", "-1", "set_message_tool_clear_delay"),
            ("_show_delay_modal", "nan", "set_message_tool_clear_delay"),
        )
        for modal_name, raw_value, method_name in invalid_values:
            with self.subTest(value=raw_value):
                view, service = await self.make_view()
                submission = await self.submit_modal(view, modal_name, raw_value)

                getattr(service, method_name).assert_not_awaited()
                self.assertTrue(submission.response.send_message.await_args.kwargs["ephemeral"])
                self.assertFalse(view._stale)

        view, service = await self.make_view()
        service.set_pixel_index_api_url.side_effect = ValueError(
            "URL must be an absolute HTTP or HTTPS URL."
        )
        submission = await self.submit_modal(view, "_show_api_url_modal", "ftp://example.test")

        service.set_pixel_index_api_url.assert_awaited_once_with("ftp://example.test")
        submission.response.send_message.assert_awaited_once_with(
            "URL must be an absolute HTTP or HTTPS URL.", ephemeral=True
        )
        self.assertFalse(view._stale)

    async def test_success_retires_old_view_and_rejects_stale_actions(self) -> None:
        view, service = await self.make_view()
        first = interaction()

        await view._toggle_messages(first)

        refreshed = first.response.edit_message.await_args.kwargs["view"]
        self.assertIsInstance(refreshed, SettingsPanelView)
        self.assertIsNot(refreshed, view)
        self.assertTrue(view._stale)
        service.set_broadcast_messages.assert_awaited_once()

        stale = interaction()
        self.assertFalse(await view.interaction_check(stale))
        service.set_broadcast_messages.assert_awaited_once()


class TestSettingsPanelCommand(unittest.IsolatedAsyncioTestCase):
    async def test_command_is_admin_only_and_slash_response_is_ephemeral(self) -> None:
        command = PixelAgentsCog.pixelagents_group.subcommands["settings"]
        self.assertEqual(command.__permissions__, {"administrator": True})

        bot = MagicMock(guilds=[])
        bot.is_owner = AsyncMock(return_value=False)
        cog = PixelAgentsCog(bot)
        cog._settings_service = settings_service()
        ctx = MagicMock()
        ctx.guild = SimpleNamespace(id=42)
        ctx.author = SimpleNamespace(id=7)
        ctx.interaction = interaction()
        ctx.send = AsyncMock()

        await cog.cmd_settings(ctx)

        call = ctx.interaction.response.send_message.await_args
        self.assertTrue(call.kwargs["ephemeral"])
        self.assertIsInstance(call.kwargs["view"], SettingsPanelView)
        self.assertEqual(call.kwargs["view"].owner_id, 7)


if __name__ == "__main__":
    unittest.main()
