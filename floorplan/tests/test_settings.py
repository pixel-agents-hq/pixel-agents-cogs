"""Focused tests for the typed settings persistence and application layer."""

from __future__ import annotations

import asyncio
import json
import unittest
from copy import deepcopy
from unittest.mock import AsyncMock, MagicMock

from floorplan.application import SettingsService
from floorplan.floorplan import Floorplan as FloorplanCog
from floorplan.infrastructure.settings import (
    CONFIG_IDENTIFIER,
    GLOBAL_DEFAULTS,
    GUILD_DEFAULTS,
    RedSettingsRepository,
)
from floorplan.tests.conftest import (
    FakeCorridor,
    _FakeClientWebSocketResponse,
    _FakeConfig,
    make_ctx,
)


def make_repository() -> tuple[RedSettingsRepository, _FakeConfig]:
    config = _FakeConfig()
    config.register_global(**deepcopy(GLOBAL_DEFAULTS))
    config.register_guild(**deepcopy(GUILD_DEFAULTS))
    return RedSettingsRepository(config), config


def make_service(
    repository: RedSettingsRepository,
) -> tuple[SettingsService, AsyncMock, AsyncMock, AsyncMock, AsyncMock]:
    clear_presence = AsyncMock()
    reauthorize = AsyncMock()
    sync_guild = AsyncMock(return_value="Sync complete.")
    despawn_guild = AsyncMock()
    service = SettingsService(
        repository,
        clear_rich_presence=clear_presence,
        reauthorize_editors=reauthorize,
        sync_guild=sync_guild,
        despawn_guild=despawn_guild,
    )
    return service, clear_presence, reauthorize, sync_guild, despawn_guild


class TestRedSettingsRepository(unittest.IsolatedAsyncioTestCase):
    def test_create_preserves_identifier_scopes_and_defaults(self) -> None:
        repository = RedSettingsRepository.create(object())
        config = repository.config

        self.assertEqual(config.identifier, CONFIG_IDENTIFIER)
        self.assertTrue(config.force_registration)
        self.assertEqual(config._global, GLOBAL_DEFAULTS)
        self.assertEqual(config._guild_defaults, GUILD_DEFAULTS)
        self.assertEqual(config._user_defaults, {})

    async def test_snapshots_and_validated_setters_are_typed(self) -> None:
        repository, _ = make_repository()

        await repository.set_ws_port(4321)
        await repository.set_message_tool_clear_delay(3)
        api_url = await repository.set_pixel_index_api_url(" https://example.com/api/ ")

        global_settings = await repository.global_settings()
        guild_settings = await repository.guild_settings(123)

        self.assertEqual(api_url, "https://example.com/api")
        self.assertEqual(global_settings.ws_port, 4321)
        self.assertEqual(global_settings.message_tool_clear_delay, 3.0)
        self.assertFalse(guild_settings.enabled)
        self.assertTrue(guild_settings.include_bots)

    async def test_seat_mutations_are_serialized_without_lost_updates(self) -> None:
        repository, _ = make_repository()

        async def add_record(agent_id: str) -> None:
            def mutation(seats: dict) -> None:
                seats[agent_id] = {"seatId": f"desk-{agent_id}"}

            await repository.mutate_guild_seats(123, mutation)

        await asyncio.gather(add_record("-1"), add_record("-2"))

        self.assertEqual(
            await repository.guild_seats(123),
            {
                "-1": {"seatId": "desk--1"},
                "-2": {"seatId": "desk--2"},
            },
        )


class TestSettingsService(unittest.IsolatedAsyncioTestCase):
    async def test_rich_presence_disable_clears_runtime_state(self) -> None:
        repository, config = make_repository()
        service, clear_presence, _, _, _ = make_service(repository)

        await service.set_broadcast_rich_presence(False)

        self.assertFalse(await config.broadcast_rich_presence())
        clear_presence.assert_awaited_once_with()

        clear_presence.reset_mock()
        await service.set_broadcast_rich_presence(True)
        clear_presence.assert_not_awaited()

    async def test_guild_changes_run_shared_side_effects(self) -> None:
        repository, _ = make_repository()
        service, _, reauthorize, sync_guild, despawn_guild = make_service(repository)

        result = await service.enable_guild(123)
        await service.disable_guild(123)

        self.assertEqual(result, "Sync complete.")
        self.assertEqual(reauthorize.await_count, 2)
        sync_guild.assert_awaited_once_with(123)
        despawn_guild.assert_awaited_once_with(123)
        self.assertFalse((await repository.guild_settings(123)).enabled)

    async def test_include_bots_only_syncs_enabled_guilds(self) -> None:
        repository, _ = make_repository()
        service, _, _, sync_guild, _ = make_service(repository)

        self.assertIsNone(await service.set_include_bots(123, False))
        sync_guild.assert_not_awaited()

        await repository.set_guild_enabled(123, True)
        self.assertEqual(await service.set_include_bots(123, True), "Sync complete.")
        sync_guild.assert_awaited_once_with(123)

    async def test_invalid_values_do_not_mutate_config(self) -> None:
        repository, _ = make_repository()
        service, _, _, _, _ = make_service(repository)
        before = await repository.global_settings()

        invalid_calls = (
            service.set_ws_port(0),
            service.set_message_tool_clear_delay(-0.1),
            service.set_message_tool_clear_delay(float("nan")),
            service.set_message_tool_clear_delay(float("inf")),
            service.set_pixel_index_api_url("ftp://example.com"),
            service.set_pixel_index_web_url("example.com"),
        )
        for call in invalid_calls:
            with self.assertRaises(ValueError):
                await call

        self.assertEqual(await repository.global_settings(), before)
        self.assertEqual(await repository.ws_port(), 3210)
        self.assertEqual(await repository.message_tool_clear_delay(), 2.0)

    async def test_repository_rejects_non_finite_delay_defensively(self) -> None:
        repository, _ = make_repository()

        for value in (float("nan"), float("inf"), float("-inf")):
            with self.assertRaises(ValueError):
                await repository.set_message_tool_clear_delay(value)

        self.assertEqual(await repository.message_tool_clear_delay(), 2.0)

    async def test_migration_seeds_every_guild_from_the_legacy_shared_layout(self) -> None:
        repository, config = make_repository()
        legacy_layout = {"version": 1, "cols": 1, "rows": 1, "tiles": [1], "furniture": []}
        legacy_seats = {"-1": {"seatId": "desk-1"}}
        await config.layout.set(legacy_layout)
        await config.seats.set(legacy_seats)

        await repository.migrate_legacy_global_layout_and_seats([100, 200])

        self.assertEqual(await repository.guild_layout(100), legacy_layout)
        self.assertEqual(await repository.guild_seats(100), legacy_seats)
        self.assertEqual(await repository.guild_layout(200), legacy_layout)

    async def test_migration_is_idempotent_and_never_clobbers_a_customized_guild_layout(
        self,
    ) -> None:
        repository, config = make_repository()
        await config.layout.set({"version": 1, "cols": 1, "rows": 1, "tiles": [1], "furniture": []})

        await repository.migrate_legacy_global_layout_and_seats([100])
        custom_layout = {"version": 1, "cols": 2, "rows": 2, "tiles": [1, 1, 1, 1], "furniture": []}
        await repository.set_guild_layout(100, custom_layout)

        # A second migration run (e.g. a reload, or a guild joining later)
        # must not overwrite a guild's own already-set layout.
        await repository.migrate_legacy_global_layout_and_seats([100])

        self.assertEqual(await repository.guild_layout(100), custom_layout)

    async def test_migration_without_a_legacy_layout_is_a_noop(self) -> None:
        repository, _ = make_repository()

        await repository.migrate_legacy_global_layout_and_seats([100])

        self.assertIsNone(await repository.guild_layout(100))


class TestSettingsCommandParity(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        bot = MagicMock()
        bot.guilds = []
        bot.is_owner = AsyncMock(return_value=False)
        self.cog = FloorplanCog(bot)
        self.cog._corridor = FakeCorridor()
        self.cog._settings_service = MagicMock()
        self.cog._settings_service.set_ws_port = AsyncMock()
        self.cog._settings_service.set_message_tool_clear_delay = AsyncMock()
        self.cog._settings_service.set_broadcast_rich_presence = AsyncMock()
        self.cog._settings_service.set_broadcast_messages = AsyncMock()
        self.cog._settings_service.enable_guild = AsyncMock(return_value="Sync complete.")
        self.cog._settings_service.disable_guild = AsyncMock()
        self.cog._settings_service.set_include_bots = AsyncMock(return_value=None)
        self.cog._settings_service.set_pixel_index_api_url = AsyncMock(
            return_value="https://api.example.com"
        )
        self.cog._settings_service.set_pixel_index_web_url = AsyncMock(
            return_value="https://web.example.com"
        )
        self.cog._check_pixel_index_health = AsyncMock(return_value=(True, "ok"))

    @staticmethod
    def context() -> MagicMock:
        return make_ctx()

    async def test_existing_commands_delegate_to_the_shared_service(self) -> None:
        ctx = self.context()

        await self.cog.cmd_wsport(ctx, 4300)
        await self.cog.cmd_richpresence(ctx, False)

        self.cog._settings_service.set_ws_port.assert_awaited_once_with(4300)
        self.cog._settings_service.set_broadcast_rich_presence.assert_awaited_once_with(False)
        self.assertEqual(ctx.send.await_count, 2)

    async def test_all_other_setting_commands_use_the_same_service(self) -> None:
        ctx = self.context()
        ctx.guild.id = 123

        await self.cog.cmd_toolcleardelay(ctx, 4.0)
        await self.cog.cmd_messages(ctx, False)
        await self.cog.cmd_enable(ctx)
        await self.cog.cmd_disable(ctx)
        await self.cog.cmd_includebots(ctx, False)
        await self.cog.cmd_pixelindex_set(ctx, "https://api.example.com/")
        await self.cog.cmd_pixelindex_setweb(ctx, "https://web.example.com/")

        self.cog._settings_service.set_message_tool_clear_delay.assert_awaited_once_with(4.0)
        self.cog._settings_service.set_broadcast_messages.assert_awaited_once_with(False)
        self.cog._settings_service.enable_guild.assert_awaited_once_with(123)
        self.cog._settings_service.disable_guild.assert_awaited_once_with(123)
        self.cog._settings_service.set_include_bots.assert_awaited_once_with(123, False)
        self.cog._settings_service.set_pixel_index_api_url.assert_awaited_once_with(
            "https://api.example.com/"
        )
        self.cog._settings_service.set_pixel_index_web_url.assert_awaited_once_with(
            "https://web.example.com/"
        )


class TestRichPresenceDisableBehavior(unittest.IsolatedAsyncioTestCase):
    async def test_command_clears_visible_tools_and_prevents_bootstrap_replay(self) -> None:
        bot = MagicMock()
        bot.guilds = []
        bot.is_owner = AsyncMock(return_value=False)
        cog = FloorplanCog(bot)
        cog._corridor = FakeCorridor()
        universe = cog._universes.get_or_create(1)
        universe.office.active_agents[(1, 10)] = ("online", "Agent")
        universe.presence.cache[(1, 10)] = "Listening to music"
        connected_socket = _FakeClientWebSocketResponse()
        cog._client_hub.add(connected_socket, guild_id=1, is_editor=False)
        ctx = TestSettingsCommandParity.context()

        await cog.cmd_richpresence(ctx, False)

        self.assertEqual(universe.presence.cache, {})
        self.assertFalse(await cog.config.broadcast_rich_presence())
        self.assertIn(
            "agentToolsClear",
            [json.loads(payload)["type"] for payload in connected_socket._sent],
        )

        new_socket = _FakeClientWebSocketResponse()
        await cog._send_bootstrap(new_socket, 1)
        bootstrap_messages = [json.loads(payload) for payload in new_socket._sent]
        self.assertFalse(any(message["type"] == "agentToolStart" for message in bootstrap_messages))
