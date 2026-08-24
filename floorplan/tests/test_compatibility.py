"""Compatibility contracts that must survive changes to the Floorplan cog."""

from __future__ import annotations

import importlib
import inspect
import json
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import floorplan as _floorplan_package
from floorplan.floorplan import Floorplan as FloorplanCog
from floorplan.tests.conftest import _FakeClientWebSocketResponse, _FakeGroup

# Pytest imports the lightweight package-level bootstrap conftest before the
# complete test stubs. Reloading here mirrors a normal Red import, where all
# runtime dependencies are available before the package is initialized.
floorplan_package = importlib.reload(_floorplan_package)
floorplan_module = importlib.import_module("floorplan.floorplan")


GLOBAL_DEFAULTS = {
    "ws_host": "0.0.0.0",
    "ws_port": 3210,
    "message_tool_clear_delay": 2.0,
    "broadcast_rich_presence": True,
    "broadcast_messages": True,
    "layout": None,
    "seats": {},
    "pixel_index_api_url": "https://pixel-index-api-staging.nntin.xyz",
    "pixel_index_web_url": "https://pixel-index.vercel.app",
    "layout_migrated_to_guild_scope": False,
    "genuine_agent_seats": {},
}
GUILD_DEFAULTS = {
    "enabled": False,
    "include_bots": True,
    "private": False,
    "layout": None,
    "seats": {},
}


def make_cog() -> FloorplanCog:
    bot = MagicMock()
    bot.guilds = []
    bot.is_owner = AsyncMock(return_value=False)
    return FloorplanCog(bot)


class TestPublicPackageContract(unittest.IsolatedAsyncioTestCase):
    async def test_setup_adds_the_exported_cog(self):
        bot = MagicMock()
        bot.guilds = []
        bot.is_owner = AsyncMock(return_value=False)
        bot.add_cog = AsyncMock()

        await floorplan_package.setup(bot)

        bot.add_cog.assert_awaited_once()
        assert isinstance(bot.add_cog.await_args.args[0], FloorplanCog)

    def test_cog_export_remains_public(self):
        assert floorplan_package.__all__ == ["Floorplan"]
        assert floorplan_package.Floorplan is FloorplanCog

    def test_runtime_metadata_requires_the_supported_versions(self):
        info = json.loads((Path(__file__).parents[1] / "info.json").read_text(encoding="utf-8"))

        assert info["min_python_version"] == [3, 11, 0]
        assert info["min_bot_version"] == "3.5.24"
        assert "pydantic>=2.11.3,<2.12" in info["requirements"]


class TestConfigContract(unittest.IsolatedAsyncioTestCase):
    def test_identifier_scopes_and_defaults_are_stable(self):
        from floorplan.infrastructure.settings import CONFIG_IDENTIFIER

        config = make_cog().config

        assert config.identifier == CONFIG_IDENTIFIER
        assert config.force_registration is True
        assert config._global == GLOBAL_DEFAULTS
        assert config._guild_defaults == GUILD_DEFAULTS
        assert config._user_defaults == {}

    async def test_guild_defaults_are_independent_and_persistent(self):
        config = make_cog().config
        first = config.guild_from_id(1)
        second = config.guild_from_id(2)

        await first.enabled.set(True)

        assert await config.guild_from_id(1).enabled() is True
        assert await second.enabled() is False
        assert await first.include_bots() is True


class TestDashboardContract(unittest.IsolatedAsyncioTestCase):
    def test_page_metadata_and_required_context_parameters_are_stable(self):
        cases = {
            "dashboard_webview": {
                "metadata": {
                    "name": None,
                    "description": "Pixel Agents server menu.",
                    "methods": ("GET",),
                },
                "required": set(),
            },
            "dashboard_session": {
                "metadata": {
                    "name": "session",
                    "description": "Pixel Agents editor session ticket.",
                    "methods": ("GET",),
                    "hidden": True,
                },
                "required": {"user_id"},
            },
            "dashboard_servers": {
                "metadata": {
                    "name": "servers",
                    "description": "Pixel Agents private server list for a session ticket.",
                    "methods": ("GET",),
                    "hidden": True,
                },
                "required": set(),
            },
            "dashboard_office": {
                "metadata": {
                    "name": "office",
                    "description": "Pixel Agents office.",
                    "methods": ("GET",),
                },
                "required": {"guild"},
            },
            "dashboard_office_login": {
                "metadata": {
                    "name": "office-login",
                    "description": "Pixel Agents office (private servers).",
                    "methods": ("GET",),
                    "hidden": True,
                },
                "required": {"user_id", "guild"},
            },
            "dashboard_static": {
                "metadata": {
                    "name": "static",
                    "description": "Pixel Agents static asset.",
                    "methods": ("GET", "HEAD"),
                },
                "required": {"asset_path"},
            },
        }

        for method_name, expected in cases.items():
            method = getattr(FloorplanCog, method_name)
            args, metadata = method.__dashboard_decorator_params__
            signature = inspect.signature(method)
            required = {
                name
                for name, parameter in signature.parameters.items()
                if name != "self"
                and parameter.default is inspect.Parameter.empty
                and parameter.kind is not inspect.Parameter.VAR_KEYWORD
            }

            assert args == ()
            assert metadata == expected["metadata"]
            assert required == expected["required"]

    async def test_dashboard_registration_uses_the_third_party_route(self):
        cog = make_cog()
        dashboard = MagicMock()

        await cog.on_dashboard_cog_add(dashboard)

        dashboard.rpc.third_parties_handler.add_third_party.assert_called_once_with(
            cog, overwrite=True
        )


class TestServerContract(unittest.IsolatedAsyncioTestCase):
    async def test_server_registers_the_websocket_and_health_routes(self):
        cog = make_cog()
        routes = []
        router = MagicMock()
        router.add_get.side_effect = lambda path, handler: routes.append((path, handler))
        app = MagicMock(router=router)
        runner = MagicMock()
        runner.setup = AsyncMock()
        site = MagicMock()
        site.start = AsyncMock()

        with (
            patch.object(floorplan_module.web, "Application", return_value=app),
            patch.object(floorplan_module.web, "AppRunner", return_value=runner),
            patch.object(floorplan_module.web, "TCPSite", return_value=site) as tcp_site,
        ):
            await cog._start_server()

        assert routes == [
            ("/ws", cog._websocket_server.handle_ws),
            ("/api/health", cog._websocket_server.handle_health),
        ]
        tcp_site.assert_called_once_with(runner, "0.0.0.0", 3210)
        runner.setup.assert_awaited_once_with()
        site.start.assert_awaited_once_with()
        assert cog._websocket_server.runner is runner

    async def test_health_response_shape_is_stable(self):
        cog = make_cog()
        cog._client_hub.add(MagicMock(), is_editor=False)
        cog._client_hub.add(MagicMock(), user_id=42, is_editor=True)
        cog._universes.get_or_create(1).office.active_agents[(1, 10)] = ("online", "One")
        cog._universes.get_or_create(2).office.active_agents[(2, 10)] = ("idle", "One")
        cog._assets = {"walls": [], "characters": []}

        response = await cog._handle_health(MagicMock())

        assert json.loads(response.text) == {
            "status": "ok",
            "clients": 2,
            # Issue #4: guilds 1 and 2 are now independent universes, so the
            # same user_id appearing in both counts twice -- no more
            # cross-guild dedup.
            "agents": 2,
            "assets": ["characters", "walls"],
        }


class TestWebSocketBootstrapContract(unittest.IsolatedAsyncioTestCase):
    async def test_full_bootstrap_order_and_wire_shapes_are_stable(self):
        cog = make_cog()
        layout = {"version": 1, "cols": 1, "rows": 1, "tiles": [1], "furniture": []}
        await cog.config.guild_from_id(100).layout.set(layout)
        cog._assets = {
            "characters": [{"down": [], "up": [], "right": []}],
            "floors": [["floor"]],
            "walls": [["wall"]],
            "carpets": [["carpet"]],
            "catalog": [{"id": "DESK"}],
            "furniture": {"DESK": ["sprite"]},
        }
        socket = _FakeClientWebSocketResponse()

        await cog._send_bootstrap(socket, 100)

        messages = [json.loads(payload) for payload in socket._sent]
        assert [message["type"] for message in messages] == [
            "providerCapabilities",
            "characterSpritesLoaded",
            "floorTilesLoaded",
            "wallTilesLoaded",
            "carpetTilesLoaded",
            "furnitureAssetsLoaded",
            "settingsLoaded",
            "areaMappingsLoaded",
            "existingAgents",
            "layoutLoaded",
        ]
        assert messages[0] == {
            "type": "providerCapabilities",
            "readingTools": ["Read", "Grep", "Glob", "WebFetch", "WebSearch"],
            "subagentToolNames": ["Task", "Agent"],
        }
        assert messages[1] == {
            "type": "characterSpritesLoaded",
            "characters": cog._assets["characters"],
        }
        assert messages[2] == {"type": "floorTilesLoaded", "sprites": cog._assets["floors"]}
        assert messages[3] == {"type": "wallTilesLoaded", "sets": cog._assets["walls"]}
        assert messages[4] == {"type": "carpetTilesLoaded", "sets": cog._assets["carpets"]}
        assert messages[5] == {
            "type": "furnitureAssetsLoaded",
            "catalog": cog._assets["catalog"],
            "sprites": cog._assets["furniture"],
        }
        assert messages[6] == {
            "type": "settingsLoaded",
            "soundEnabled": False,
            "lastSeenVersion": "",
            "extensionVersion": "",
            "watchAllSessions": False,
            "alwaysShowLabels": False,
            "ghostHeadlessAgents": True,
            "hooksEnabled": False,
            "hooksInfoShown": True,
            "externalAssetDirectories": [],
            "showAreas": False,
        }
        assert messages[7] == {"type": "areaMappingsLoaded", "mappings": {}}
        assert messages[8] == {
            "type": "existingAgents",
            "agents": [],
            "agentMeta": {},
            "folderNames": {},
            "externalAgents": {},
            "headlessAgents": {},
        }
        assert messages[9] == {"type": "layoutLoaded", "layout": layout}

    async def test_unknown_client_messages_remain_ignored(self):
        cog = make_cog()
        socket = _FakeClientWebSocketResponse()
        cog._client_hub.add(socket, is_editor=False)

        await cog._handle_client_message(socket, {"type": "futureMessage", "value": 1})

        assert socket._sent == []
        assert cog._clients[socket].is_editor is False


class TestCommandContract:
    def test_command_tree_and_names_are_stable(self):
        root = FloorplanCog.floorplan_group

        assert isinstance(root, _FakeGroup)
        assert root.command_metadata == {"name": "floorplan", "invoke_without_command": True}
        assert root.__wrapped__.__guild_only__ is True
        assert set(root.subcommands) == {
            "status",
            "settings",
            "wsport",
            "toolcleardelay",
            "richpresence",
            "messages",
            "enable",
            "disable",
            "includebots",
            "sync",
            "despawnall",
            "private",
            "index",
            "layout",
        }
        assert set(root.subcommands["index"].subcommands) == {"set", "setweb"}
        assert set(root.subcommands["layout"].subcommands) == {"search", "view"}

    def test_administrative_permissions_are_stable(self):
        root = FloorplanCog.floorplan_group
        direct_admin_commands = {
            "status",
            "settings",
            "wsport",
            "toolcleardelay",
            "richpresence",
            "messages",
            "enable",
            "disable",
            "includebots",
            "sync",
            "despawnall",
        }

        for name in direct_admin_commands:
            assert root.subcommands[name].__permissions__ == {"administrator": True}
        # "private" is deliberately Manage-Server-gated, not administrator-only
        # -- issue #4 asks for the Discord "Manage Server" permission, not the
        # bot-admin tier every other setting here uses.
        assert root.subcommands["private"].__permissions__ == {"manage_guild": True}
        assert root.subcommands["index"].__wrapped__.__permissions__ == {"administrator": True}
        assert root.subcommands["index"].subcommands["set"].__permissions__ == {
            "administrator": True
        }
        assert root.subcommands["index"].subcommands["setweb"].__permissions__ == {
            "administrator": True
        }
        assert not hasattr(root.subcommands["layout"].subcommands["search"], "__permissions__")
        assert not hasattr(root.subcommands["layout"].subcommands["view"], "__permissions__")
