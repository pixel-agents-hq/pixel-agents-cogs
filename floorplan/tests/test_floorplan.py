"""Unit tests for the floorplan cog.

Stubs for discord / redbot / aiohttp are installed by conftest.py.
"""
from __future__ import annotations

import asyncio
import base64
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, MagicMock, patch

from corridor.domain import ReplyField
from floorplan.application.catalogue import CatalogueResult
from floorplan.models import LayoutDetail, LayoutListResponse
from floorplan.floorplan import (
    _discord_id_to_agent_id,
    _JS_MAX_SAFE,
    _VISIBLE_STATUSES,
    _LayoutBrowseView,
    _LayoutDetailView,
    Floorplan as FloorplanCog,
)
from floorplan.infrastructure.tickets import TicketStore
from floorplan.infrastructure.webview import WebviewAssetProvider
from floorplan.tests.conftest import (
    FakeCorridor,
    FakePixelAgents,
    _FakeClientWebSocketResponse,
    _FakeInteraction,
)

import discord  # stubbed by conftest
import aiohttp  # stubbed by conftest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _activity(activity_type, name="Some Game"):
    a = MagicMock()
    a.type = activity_type
    # `name` must be a real string: MagicMock's auto-attribute is not JSON
    # serializable, and presence labels are serialized onto the wire.
    a.name = name
    return a


def _member(guild_id=100, user_id=1, display_name="Tin", status="online",
            is_bot=False, activities=()):
    m = MagicMock()
    m.guild.id = guild_id
    m.id = user_id
    m.display_name = display_name
    m.status = status
    m.bot = is_bot
    m.activities = list(activities)
    return m


def _make_cog():
    bot = MagicMock()
    bot.guilds = []
    bot.is_owner = AsyncMock(return_value=False)
    cog = FloorplanCog(bot)
    # Commands reply through corridor's shared ReplyMode (see
    # floorplan/adapters/replies.py) -- default to a text-mode double so
    # tests that don't care about ReplyMode still get a working `_corridor`.
    cog._corridor = FakeCorridor()
    # _sync_webview_assets() normally resolves this lazily via
    # corridor.dependency_loader.ensure_loaded() on first use; default it to a
    # ready-and-loaded double the same way _corridor is defaulted above so
    # tests that don't care about the webview still get a working one.
    cog._pixelagents._cog = FakePixelAgents()
    return cog


def _connect(cog, authorized=False):
    """Attach a fake office client to the cog and return it."""
    socket = _FakeClientWebSocketResponse()
    cog._client_hub.add(socket, is_editor=authorized)
    return socket


def _sent_types(socket):
    return [json.loads(raw)["type"] for raw in socket._sent]


def _make_enabled_cog():
    cog = _make_cog()

    class _EnabledGuildConfig:
        def __getattr__(self, name):
            from floorplan.tests.conftest import _FakeGuildConfigAttr
            data = {"enabled": True, "include_bots": True}
            return _FakeGuildConfigAttr(data, name)

    cog.config.guild = lambda guild: _EnabledGuildConfig()
    return cog


def _valid_layout():
    return {
        "version": 1,
        "cols": 2,
        "rows": 2,
        "tiles": [1, 1, 1, 1],
        "furniture": [],
    }


# ---------------------------------------------------------------------------
# Tests: ID mapping
# ---------------------------------------------------------------------------

class TestDiscordIdToAgentId(unittest.TestCase):
    def test_output_is_negative(self):
        for uid in (1, 123456789, 987654321012345678):
            self.assertLess(_discord_id_to_agent_id(uid), 0)

    def test_within_js_safe_range(self):
        for uid in (1, 123456789, 987654321012345678):
            result = _discord_id_to_agent_id(uid)
            self.assertGreaterEqual(result, -_JS_MAX_SAFE)

    def test_stable_across_calls(self):
        uid = 123456789012345678
        self.assertEqual(_discord_id_to_agent_id(uid), _discord_id_to_agent_id(uid))

    def test_different_users_different_ids(self):
        self.assertNotEqual(_discord_id_to_agent_id(1), _discord_id_to_agent_id(2))

    def test_zero_modulo_edge_case(self):
        result = _discord_id_to_agent_id(_JS_MAX_SAFE)
        self.assertEqual(result, -_JS_MAX_SAFE)


# ---------------------------------------------------------------------------
# Tests: visible statuses
# ---------------------------------------------------------------------------

class TestVisibleStatuses(unittest.TestCase):
    def test_visible(self):
        for s in ("online", "idle", "dnd"):
            self.assertIn(s, _VISIBLE_STATUSES)

    def test_not_visible(self):
        for s in ("offline", "invisible"):
            self.assertNotIn(s, _VISIBLE_STATUSES)


# ---------------------------------------------------------------------------
# Tests: status/inclusion helpers
# ---------------------------------------------------------------------------

class TestStatusMapping(unittest.TestCase):
    def setUp(self):
        self.cog = _make_cog()

    def test_online_is_visible(self):
        self.assertEqual(self.cog._status_str(_member(status="online")), "online")

    def test_idle_is_visible(self):
        self.assertEqual(self.cog._status_str(_member(status="idle")), "idle")

    def test_dnd_is_visible(self):
        self.assertEqual(self.cog._status_str(_member(status="dnd")), "dnd")

    def test_offline_is_none(self):
        self.assertIsNone(self.cog._status_str(_member(status="offline")))

    def test_invisible_is_none(self):
        self.assertIsNone(self.cog._status_str(_member(status="invisible")))


class TestBotInclusion(unittest.TestCase):
    def setUp(self):
        self.cog = _make_cog()

    def test_bot_included_by_default(self):
        self.assertTrue(self.cog._is_included(_member(is_bot=True), include_bots=True))

    def test_bot_excluded_when_disabled(self):
        self.assertFalse(self.cog._is_included(_member(is_bot=True), include_bots=False))

    def test_human_always_included(self):
        self.assertTrue(self.cog._is_included(_member(is_bot=False), include_bots=False))


class TestRichPresence(unittest.TestCase):
    def setUp(self):
        self.cog = _make_cog()

    def test_no_activities_is_waiting(self):
        m = _member(activities=[])
        self.assertEqual(self.cog._agent_status(m), "waiting")

    def test_game_activity_is_active(self):
        m = _member(activities=[_activity(discord.ActivityType.playing)])
        self.assertEqual(self.cog._agent_status(m), "active")

    def test_custom_activity_only_is_waiting(self):
        m = _member(activities=[_activity(discord.ActivityType.custom)])
        self.assertEqual(self.cog._agent_status(m), "waiting")

    def test_custom_plus_game_is_active(self):
        m = _member(activities=[
            _activity(discord.ActivityType.custom),
            _activity(discord.ActivityType.playing),
        ])
        self.assertEqual(self.cog._agent_status(m), "active")


# ---------------------------------------------------------------------------
# Tests: WebSocket send
# ---------------------------------------------------------------------------

class TestSend(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.cog = _make_cog()
        self.ws = _connect(self.cog)

    async def test_send_serializes_json(self):
        await self.cog._send({"type": "agentClosed", "id": -42})
        self.assertEqual(len(self.ws._sent), 1)
        parsed = json.loads(self.ws._sent[0])
        self.assertEqual(parsed["type"], "agentClosed")

    async def test_send_reaches_every_client(self):
        other = _connect(self.cog)
        await self.cog._send({"type": "agentClosed", "id": -42})
        self.assertEqual(len(self.ws._sent), 1)
        self.assertEqual(len(other._sent), 1)

    async def test_send_noop_when_no_clients(self):
        self.cog._clients.clear()
        await self.cog._send({"type": "test"})

    async def test_send_drops_closed_clients(self):
        self.ws.closed = True
        await self.cog._send({"type": "test"})
        self.assertEqual(len(self.ws._sent), 0)
        self.assertNotIn(self.ws, self.cog._clients)


# ---------------------------------------------------------------------------
# Tests: bootstrap on webviewReady
# ---------------------------------------------------------------------------

class TestBootstrap(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.cog = _make_cog()
        self.cog._assets = {
            "characters": [{"down": [], "up": [], "right": []}],
            "floors": [[["#000"]]],
            "walls": [[[["#000"]]]],
            "carpets": [[[["#000"]]]],
            "furniture": {"DESK": [["#000"]]},
            "catalog": [{"id": "DESK", "name": "Desk", "label": "Desk", "category": "desks",
                         "file": "DESK.png", "width": 16, "height": 16, "footprintW": 1,
                         "footprintH": 1, "isDesk": True, "canPlaceOnWalls": False}],
        }
        self.cog._default_layout = lambda: _valid_layout()
        self.ws = _connect(self.cog)

    async def test_capabilities_arrive_first(self):
        await self.cog._send_bootstrap(self.ws)
        self.assertEqual(_sent_types(self.ws)[0], "providerCapabilities")

    async def test_layout_arrives_after_existing_agents(self):
        """The webview buffers existingAgents and only builds characters on
        layoutLoaded, so a layout-first bootstrap renders an empty office."""
        await self.cog._send_bootstrap(self.ws)
        types = _sent_types(self.ws)
        self.assertLess(types.index("existingAgents"), types.index("layoutLoaded"))

    async def test_sends_every_asset_family(self):
        await self.cog._send_bootstrap(self.ws)
        types = _sent_types(self.ws)
        for expected in (
            "characterSpritesLoaded", "floorTilesLoaded", "wallTilesLoaded",
            "carpetTilesLoaded", "furnitureAssetsLoaded", "settingsLoaded",
        ):
            self.assertIn(expected, types)

    async def test_replays_presence_bubbles_after_layout(self):
        self.cog._agents[(100, 1)] = ("online", "Tin")
        self.cog._presence_cache[(100, 1)] = "Spotify"
        await self.cog._send_bootstrap(self.ws)
        types = _sent_types(self.ws)
        self.assertGreater(types.index("agentToolStart"), types.index("layoutLoaded"))


class TestPixelagentsResolutionIsLazy(unittest.IsolatedAsyncioTestCase):
    """cog_load() must never *synchronously await* resolving pixelagents --
    see corridor.dependency_loader.LazyDependency's docstring.
    A cog that eagerly cross-loads a dependency as part of its own
    cog_load() transaction makes that load/unload cycle no longer
    self-contained, which broke the Downloader RPC smoke test
    (check-cogs.yml): pixelagents got auto-loaded as a side effect of
    loading floorplan, so when the harness later loaded pixelagents
    explicitly (its own cogs are tested alphabetically, independently of
    each other), Red reported it under `alreadyloaded_packages` instead of
    `loaded_packages` and the harness treated that as a failure. cog_load()
    does schedule a *backgrounded* eager load (see
    test_cog_load_schedules_an_eager_pixelagents_load_in_the_background
    below) so `[p]cogs` reflects the dependency promptly in normal
    operation -- it just doesn't block cog_load() itself on it."""

    async def test_cog_load_never_touches_pixelagents(self) -> None:
        bot = MagicMock()
        bot.guilds = []
        bot.is_owner = AsyncMock(return_value=False)
        bot.wait_until_red_ready = AsyncMock()
        cog = FloorplanCog(bot)
        cog._corridor = FakeCorridor()

        with (
            patch(
                "corridor.dependency_loader.ensure_loaded", new=AsyncMock()
            ) as ensure_loaded,
            patch.object(cog, "_start_server", new=AsyncMock()),
            patch.object(cog._pixel_index_client, "start", new=AsyncMock()),
        ):
            await cog.cog_load()

        ensure_loaded.assert_not_awaited()
        self.assertIsNone(cog._pixelagents.value)
        await cog.cog_unload()

    async def test_cog_load_schedules_an_eager_pixelagents_load_in_the_background(self) -> None:
        bot = MagicMock()
        bot.guilds = []
        bot.is_owner = AsyncMock(return_value=False)
        bot.wait_until_red_ready = AsyncMock()
        cog = FloorplanCog(bot)
        cog._corridor = FakeCorridor()
        pixelagents_double = FakePixelAgents()

        with (
            patch(
                "corridor.dependency_loader.ensure_loaded",
                new=AsyncMock(return_value=pixelagents_double),
            ) as ensure_loaded,
            patch.object(cog, "_start_server", new=AsyncMock()),
            patch.object(cog._pixel_index_client, "start", new=AsyncMock()),
        ):
            await cog.cog_load()
            # cog_load() only *schedules* the eager load; give the event
            # loop a turn so the backgrounded task actually runs.
            await asyncio.sleep(0)

        ensure_loaded.assert_awaited_once_with(bot, "pixelagents", "PixelAgents")
        self.assertIs(cog._pixelagents.value, pixelagents_double)
        await cog.cog_unload()

    async def test_sync_webview_assets_resolves_pixelagents_on_first_use_only(self) -> None:
        cog = _make_cog()
        cog._pixelagents._cog = None
        pixelagents_double = FakePixelAgents()

        with patch(
            "corridor.dependency_loader.ensure_loaded",
            new=AsyncMock(return_value=pixelagents_double),
        ) as ensure_loaded:
            await cog._sync_webview_assets()
            await cog._sync_webview_assets()

        ensure_loaded.assert_awaited_once_with(cog.bot, "pixelagents", "PixelAgents")
        self.assertIs(cog._pixelagents.value, pixelagents_double)

    async def test_setup_never_touches_pixelagents_either(self) -> None:
        """The same bug also lived one layer up: floorplan/__init__.py's
        setup() -- Red's actual `[p]load floorplan` entrypoint -- used to
        call the pixelagents full-loader directly, before cog_load even
        runs. Fixing only cog_base.py's cog_load() was not sufficient.

        setup() does need pixelagents genuinely *importable* though (its
        agent-visualization modules are imported at module scope by
        `.floorplan`) -- see corridor.dependency_loader.ensure_importable,
        which stops short of the full Cog load this test guards against."""

        import floorplan as floorplan_package

        bot = MagicMock()
        bot.guilds = []
        bot.is_owner = AsyncMock(return_value=False)
        bot.add_cog = AsyncMock()

        with (
            patch(
                "corridor.dependency_loader.ensure_loaded", new=AsyncMock()
            ) as ensure_loaded,
            patch(
                "floorplan.dependency_loader.ensure_corridor_loaded", new=AsyncMock()
            ),
            patch(
                "corridor.dependency_loader.ensure_importable",
                new=AsyncMock(),
            ) as ensure_importable,
        ):
            await floorplan_package.setup(bot)

        ensure_loaded.assert_not_awaited()
        ensure_importable.assert_awaited_once_with(bot, "pixelagents")


class TestCogLoadCleansUpOnFailure(unittest.IsolatedAsyncioTestCase):
    """Regression test for a real incident: Red only calls cog_unload() for
    a cog_load() that returned without raising (bot.extensions[name] is set
    *after* setup() succeeds) -- so a crash partway through cog_load(),
    after _start_server() already bound the office port, used to leak that
    socket forever, since nothing was ever left to call .stop() on it.
    cog_load() now cleans up (via cog_unload()) anything it already
    started before letting the error propagate."""

    async def test_a_crash_after_start_server_still_stops_the_server(self) -> None:
        bot = MagicMock()
        bot.guilds = []
        bot.is_owner = AsyncMock(return_value=False)
        cog = FloorplanCog(bot)
        cog._corridor = FakeCorridor()

        def _boom(coroutine, **kwargs):
            coroutine.close()  # avoid a "coroutine was never awaited" warning
            raise RuntimeError("boom")

        with (
            patch.object(cog, "_start_server", new=AsyncMock()) as start_server,
            patch.object(cog._pixel_index_client, "start", new=AsyncMock()),
            patch.object(cog._websocket_server, "stop", new=AsyncMock()) as stop_server,
            patch.object(cog._task_supervisor, "create", side_effect=_boom),
        ):
            with self.assertRaisesRegex(RuntimeError, "boom"):
                await cog.cog_load()

        start_server.assert_awaited_once()
        stop_server.assert_awaited_once()


class TestWebviewBasePathMismatch(unittest.IsolatedAsyncioTestCase):
    """A build that predates the shared relative-asset convention must
    surface visibly, not just 404 quietly in a browser console -- see
    adapters/cog_base.py's EXPECTED_RELATIVE_BASE_PATH."""

    async def test_current_convention_reports_loaded(self) -> None:
        cog = _make_cog()
        cog._pixelagents._cog = FakePixelAgents(built_base_path="./")

        await cog._sync_webview_assets()
        cog._assets["characters"] = ["fake"]  # bypass the empty fixture dist's real (empty) load

        self.assertEqual(cog._webview_assets_status(), "✅ loaded")

    async def test_outdated_convention_is_reported_instead_of_loaded(self) -> None:
        cog = _make_cog()
        cog._pixelagents._cog = FakePixelAgents(built_base_path="/third-party/pixelagents/static/")

        await cog._sync_webview_assets()

        status = cog._webview_assets_status()
        self.assertIn("outdated convention", status)
        self.assertIn("[p]pixelagents webview rebuild", status)

    async def test_a_missing_built_base_path_is_not_treated_as_a_mismatch(self) -> None:
        """Defensive: an older pixelagents that predates built_base_path
        shouldn't make every floorplan report a false mismatch."""
        status = FakePixelAgents().webview_bundle_status()
        del status.built_base_path

        class _StaleStatusPixelAgents:
            def webview_bundle_status(self_inner):
                return status

        cog = _make_cog()
        cog._pixelagents._cog = _StaleStatusPixelAgents()

        await cog._sync_webview_assets()
        cog._assets["characters"] = ["fake"]  # bypass the empty fixture dist's real (empty) load

        self.assertEqual(cog._webview_assets_status(), "✅ loaded")


# ---------------------------------------------------------------------------
# Tests: dashboard webview hosting
# ---------------------------------------------------------------------------

class TestDashboardWebviewHosting(unittest.IsolatedAsyncioTestCase):
    async def test_static_asset_returns_raw_response(self):
        cog = _make_cog()
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "assets").mkdir()
            (root / "assets" / "index-test.js").write_text("console.log('ok');", encoding="utf-8")
            cog._webview_assets = WebviewAssetProvider(root)

            result = await cog.dashboard_static("assets/index-test.js")

        self.assertEqual(result["status"], 0)
        raw = result["raw_response"]
        self.assertEqual(raw["content_type"], "text/javascript; charset=utf-8")
        self.assertEqual(raw["headers"]["Cache-Control"], "public, max-age=3600")
        self.assertEqual(base64.b64decode(raw["body_base64"]).decode("utf-8"), "console.log('ok');")

    async def test_static_asset_rejects_path_traversal(self):
        cog = _make_cog()
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "index.html").write_text("ok", encoding="utf-8")
            cog._webview_assets = WebviewAssetProvider(root)

            result = await cog.dashboard_static("../index.html")

        self.assertEqual(result["status"], 1)
        self.assertEqual(result["error_code"], 404)

    async def test_dashboard_webview_returns_index_html(self):
        cog = _make_cog()
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "index.html").write_text("<!doctype html><div id=\"root\"></div>", encoding="utf-8")
            cog._webview_assets = WebviewAssetProvider(root)
            cog._pixelagents._cog = FakePixelAgents(dist_path=root)

            result = await cog.dashboard_webview()

        self.assertEqual(result["status"], 0)
        self.assertTrue(result["web_content"]["standalone"])
        self.assertIn("root", result["web_content"]["source"])


# ---------------------------------------------------------------------------
# Tests: send_existing_agents
# ---------------------------------------------------------------------------

class TestSendExistingAgents(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.cog = _make_cog()
        self.ws = _connect(self.cog)

    async def test_empty_agents(self):
        await self.cog._send_existing_agents()
        msg = json.loads(self.ws._sent[0])
        self.assertEqual(msg["type"], "existingAgents")
        self.assertEqual(msg["agents"], [])

    async def test_single_agent(self):
        self.cog._agents[(100, 1)] = ("online", "Tin")
        await self.cog._send_existing_agents()
        msg = json.loads(self.ws._sent[0])
        expected_id = _discord_id_to_agent_id(1)
        self.assertIn(expected_id, msg["agents"])
        self.assertEqual(msg["folderNames"][str(expected_id)], "online")

    async def test_same_user_two_guilds_deduplicated(self):
        self.cog._agents[(100, 1)] = ("online", "Tin")
        self.cog._agents[(200, 1)] = ("idle", "Tin")
        await self.cog._send_existing_agents()
        msg = json.loads(self.ws._sent[0])
        self.assertEqual(len(msg["agents"]), 1)


# ---------------------------------------------------------------------------
# Tests: reconcile member
# ---------------------------------------------------------------------------

class TestReconcileMember(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.cog = _make_cog()
        self.ws = _connect(self.cog)

    async def test_new_visible_member_spawns(self):
        m = _member(status="online")
        await self.cog._reconcile_member(m, include_bots=True)
        sent_types = [json.loads(s)["type"] for s in self.ws._sent]
        self.assertIn("agentCreated", sent_types)

    async def test_spawn_sets_folder_name(self):
        m = _member(status="dnd")
        await self.cog._reconcile_member(m, include_bots=True)
        created = next(json.loads(s) for s in self.ws._sent if json.loads(s)["type"] == "agentCreated")
        self.assertEqual(created["folderName"], "dnd")

    async def test_spawn_sets_agent_name_via_team_info(self):
        m = _member(status="online", display_name="Alice")
        await self.cog._reconcile_member(m, include_bots=True)
        team_info = next(json.loads(s) for s in self.ws._sent if json.loads(s)["type"] == "agentTeamInfo")
        self.assertEqual(team_info["agentName"], "Alice")

    async def test_spawn_sends_status(self):
        m = _member(status="online", activities=[_activity(discord.ActivityType.playing)])
        await self.cog._reconcile_member(m, include_bots=True)
        status_msg = next(json.loads(s) for s in self.ws._sent if json.loads(s)["type"] == "agentStatus")
        self.assertEqual(status_msg["status"], "active")

    async def test_offline_member_not_spawned(self):
        m = _member(status="offline")
        await self.cog._reconcile_member(m, include_bots=True)
        sent_types = [json.loads(s)["type"] for s in self.ws._sent]
        self.assertNotIn("agentCreated", sent_types)

    async def test_offline_cached_member_closed(self):
        self.cog._agents[(100, 1)] = ("online", "Tin")
        m = _member(status="offline")
        await self.cog._reconcile_member(m, include_bots=True)
        sent_types = [json.loads(s)["type"] for s in self.ws._sent]
        self.assertIn("agentClosed", sent_types)
        self.assertNotIn((100, 1), self.cog._agents)

    async def test_folder_change_closes_and_respawns(self):
        self.cog._agents[(100, 1)] = ("online", "Tin")
        m = _member(status="dnd")
        await self.cog._reconcile_member(m, include_bots=True)
        sent_types = [json.loads(s)["type"] for s in self.ws._sent]
        self.assertIn("agentClosed", sent_types)
        self.assertIn("agentCreated", sent_types)

    async def test_name_change_only_sends_team_info(self):
        self.cog._agents[(100, 1)] = ("online", "Tin")
        m = _member(status="online", display_name="Newname")
        await self.cog._reconcile_member(m, include_bots=True)
        sent_types = [json.loads(s)["type"] for s in self.ws._sent]
        self.assertNotIn("agentClosed", sent_types)
        self.assertNotIn("agentCreated", sent_types)
        self.assertIn("agentTeamInfo", sent_types)

    async def test_no_change_sends_nothing(self):
        self.cog._agents[(100, 1)] = ("online", "Tin")
        m = _member(status="online", display_name="Tin")
        await self.cog._reconcile_member(m, include_bots=True)
        self.assertEqual(len(self.ws._sent), 0)

    async def test_bot_excluded_when_include_bots_false(self):
        m = _member(status="online", is_bot=True)
        await self.cog._reconcile_member(m, include_bots=False)
        self.assertNotIn((100, 1), self.cog._agents)

    async def test_bot_cached_excluded_closes(self):
        self.cog._agents[(100, 99)] = ("online", "BotName")
        m = _member(user_id=99, status="online", is_bot=True)
        await self.cog._reconcile_member(m, include_bots=False)
        sent_types = [json.loads(s)["type"] for s in self.ws._sent]
        self.assertIn("agentClosed", sent_types)


# ---------------------------------------------------------------------------
# Tests: close agent
# ---------------------------------------------------------------------------

class TestCloseAgent(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.cog = _make_cog()
        self.ws = _connect(self.cog)

    async def test_close_sends_agent_closed(self):
        self.cog._agents[(100, 1)] = ("online", "Tin")
        await self.cog._close_agent(100, 1)
        sent_types = [json.loads(s)["type"] for s in self.ws._sent]
        self.assertIn("agentClosed", sent_types)

    async def test_close_removes_from_registry(self):
        self.cog._agents[(100, 1)] = ("online", "Tin")
        await self.cog._close_agent(100, 1)
        self.assertNotIn((100, 1), self.cog._agents)

    async def test_close_nonexistent_is_noop(self):
        await self.cog._close_agent(100, 999)
        self.assertEqual(len(self.ws._sent), 0)

    async def test_close_user_active_in_other_guild_does_not_send_closed(self):
        self.cog._agents[(100, 1)] = ("online", "Tin")
        self.cog._agents[(200, 1)] = ("idle", "Tin")
        await self.cog._close_agent(100, 1)
        sent_types = [json.loads(s)["type"] for s in self.ws._sent]
        self.assertNotIn("agentClosed", sent_types)


# ---------------------------------------------------------------------------
# Tests: auth check
# ---------------------------------------------------------------------------

class TestCheckAuth(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.cog = _make_cog()

    def _enable_guild(self, guild):
        async def _enabled():
            return True

        guild_cfg = MagicMock()
        guild_cfg.enabled = _enabled
        self.cog.config.guild = MagicMock(return_value=guild_cfg)
        self.cog.bot.guilds = [guild]

    async def test_zero_user_id_denied(self):
        self.assertFalse(await self.cog._check_auth(0))

    async def test_bot_owner_allowed(self):
        self.cog.bot.is_owner = AsyncMock(return_value=True)
        self.assertTrue(await self.cog._check_auth(12345))

    async def test_keyholder_denied_when_no_guild_membership(self):
        self.cog._corridor = FakeCorridor(keyholders=frozenset({12345}))
        self.cog.bot.guilds = []

        self.assertFalse(await self.cog._check_auth(12345))

    async def test_keyholder_allows(self):
        self.cog._corridor = FakeCorridor(keyholders=frozenset({12345}))
        member = MagicMock()
        member.id = 12345

        guild = MagicMock()
        guild.get_member = MagicMock(return_value=member)
        self._enable_guild(guild)

        self.assertTrue(await self.cog._check_auth(12345))
        guild.fetch_member.assert_not_called()
        self.assertIn((12345, "keyholder"), self.cog._corridor.capability_checks)

    async def test_uncached_keyholder_fetches_member_and_allows(self):
        self.cog._corridor = FakeCorridor(keyholders=frozenset({12345}))
        member = MagicMock()
        member.id = 12345

        guild = MagicMock()
        guild.get_member = MagicMock(return_value=None)
        guild.fetch_member = AsyncMock(return_value=member)
        self._enable_guild(guild)

        self.assertTrue(await self.cog._check_auth(12345))
        guild.fetch_member.assert_awaited_once_with(12345)

    async def test_corridor_owner_allows_without_keyholder_role(self):
        self.cog._corridor = FakeCorridor(owners=frozenset({12345}))
        member = MagicMock()
        member.id = 12345

        guild = MagicMock()
        guild.get_member = MagicMock(return_value=member)
        self._enable_guild(guild)

        self.assertTrue(await self.cog._check_auth(12345))

    async def test_non_keyholder_denied(self):
        self.cog._corridor = FakeCorridor(keyholders=frozenset({999}))
        member = MagicMock()
        member.id = 12345

        guild = MagicMock()
        guild.get_member = MagicMock(return_value=member)
        self._enable_guild(guild)

        self.assertFalse(await self.cog._check_auth(12345))

    async def test_uncached_member_fetch_failure_denied(self):
        self.cog._corridor = FakeCorridor(keyholders=frozenset({12345}))
        guild = MagicMock()
        guild.get_member = MagicMock(return_value=None)
        guild.fetch_member = AsyncMock(side_effect=Exception("not found"))
        self._enable_guild(guild)

        self.assertFalse(await self.cog._check_auth(12345))
        guild.fetch_member.assert_awaited_once_with(12345)

    async def test_disabled_guild_is_skipped(self):
        self.cog._corridor = FakeCorridor(keyholders=frozenset({12345}))
        member = MagicMock()
        member.id = 12345

        guild = MagicMock()
        guild.get_member = MagicMock(return_value=member)

        async def _disabled():
            return False

        guild_cfg = MagicMock()
        guild_cfg.enabled = _disabled
        self.cog.config.guild = MagicMock(return_value=guild_cfg)
        self.cog.bot.guilds = [guild]

        self.assertFalse(await self.cog._check_auth(12345))


# ---------------------------------------------------------------------------
# Tests: handle_server_message
# ---------------------------------------------------------------------------

class TestHandleClientMessage(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.cog = _make_cog()
        self.viewer = _connect(self.cog, authorized=False)
        self.editor = _connect(self.cog, authorized=True)

    async def test_webview_ready_triggers_bootstrap(self):
        self.cog._send_bootstrap = AsyncMock()
        await self.cog._handle_client_message(self.viewer, {"type": "webviewReady"})
        self.cog._send_bootstrap.assert_awaited_once_with(self.viewer)

    async def test_viewer_cannot_save_layout(self):
        await self.cog._handle_client_message(
            self.viewer, {"type": "saveLayout", "layout": _valid_layout()}
        )
        self.assertIsNone(await self.cog.config.layout())

    async def test_editor_can_save_layout(self):
        layout = _valid_layout()
        await self.cog._handle_client_message(self.editor, {"type": "saveLayout", "layout": layout})
        self.assertEqual(await self.cog.config.layout(), layout)

    async def test_saved_layout_is_mirrored_to_other_tabs(self):
        await self.cog._handle_client_message(
            self.editor, {"type": "saveLayout", "layout": _valid_layout()}
        )
        self.assertIn("layoutLoaded", _sent_types(self.viewer))
        # The saving client already applied it locally; echoing would be noise.
        self.assertEqual(_sent_types(self.editor), [])

    async def test_invalid_layout_is_rejected(self):
        await self.cog._handle_client_message(
            self.editor, {"type": "saveLayout", "layout": {"version": 99}}
        )
        self.assertIsNone(await self.cog.config.layout())

    async def test_viewer_cannot_save_seats(self):
        await self.cog._handle_client_message(
            self.viewer, {"type": "saveAgentSeats", "seats": {"-1": {"seatId": "a"}}}
        )
        self.assertEqual(await self.cog.config.seats(), {})

    async def test_editor_seats_are_persisted(self):
        await self.cog._handle_client_message(
            self.editor,
            {"type": "saveAgentSeats", "seats": {"-1": {"seatId": "chair:1", "palette": 2}}},
        )
        seats = await self.cog.config.seats()
        self.assertEqual(seats["-1"]["seatId"], "chair:1")
        self.assertEqual(seats["-1"]["palette"], 2)

    async def test_out_of_range_palette_is_ignored(self):
        await self.cog._handle_client_message(
            self.editor, {"type": "saveAgentSeats", "seats": {"-1": {"palette": 999}}}
        )
        self.assertNotIn("palette", (await self.cog.config.seats())["-1"])

    async def test_authorize_with_valid_ticket_upgrades_viewer_to_editor(self):
        self.cog._check_auth = AsyncMock(return_value=True)
        ticket = self.cog._mint_ticket(4242)
        await self.cog._handle_client_message(self.viewer, {"type": "authorize", "ticket": ticket})
        state = self.cog._clients[self.viewer]
        self.assertTrue(state.is_editor)
        self.assertEqual(state.user_id, 4242)

    async def test_authorize_with_unknown_ticket_stays_viewer(self):
        await self.cog._handle_client_message(self.viewer, {"type": "authorize", "ticket": "nope"})
        self.assertFalse(self.cog._clients[self.viewer].is_editor)

    async def test_authorize_with_valid_ticket_but_failed_authz_stays_viewer(self):
        self.cog._check_auth = AsyncMock(return_value=False)
        ticket = self.cog._mint_ticket(4242)
        await self.cog._handle_client_message(self.viewer, {"type": "authorize", "ticket": ticket})
        state = self.cog._clients[self.viewer]
        self.assertFalse(state.is_editor)
        self.assertEqual(state.user_id, 4242)


class TestTicketInjection(unittest.IsolatedAsyncioTestCase):
    async def _render(self, html):
        cog = _make_cog()
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "index.html").write_text(html, encoding="utf-8")
            cog._webview_assets = WebviewAssetProvider(root)
            cog._pixelagents._cog = FakePixelAgents(dist_path=root)
            result = await cog.dashboard_webview()
        return cog, result["web_content"]["source"]

    async def test_shim_is_injected_before_the_bundle(self):
        html = '<!doctype html><head><script src="/app.js"></script></head><body></body>'
        _, source = await self._render(html)
        # The constructor must be patched before the module bundle runs, or the
        # socket is opened without a chance to be authorized.
        self.assertLess(source.index("window.WebSocket = Patched"), source.index("/app.js"))

    async def test_webview_page_does_not_mint_a_ticket(self):
        # The webview page is public and must not know the visitor's Discord
        # ID; tickets are only minted by the login-gated `session` page.
        cog, _source = await self._render("<!doctype html><head></head><body></body>")
        self.assertEqual(cog._ticket_store.tickets, {})

    async def test_shim_fetches_the_session_page(self):
        _, source = await self._render("<!doctype html><head></head><body></body>")
        self.assertIn("/session", source)

    async def test_headless_document_still_gets_the_shim(self):
        _, source = await self._render("<div id='root'></div>")
        self.assertIn("window.WebSocket = Patched", source)


class TestSessionTicketPage(unittest.IsolatedAsyncioTestCase):
    async def test_session_page_mints_a_ticket_for_the_visitor(self):
        cog = _make_cog()
        result = await cog.dashboard_session(user_id=777)
        self.assertEqual(result["status"], 0)
        body = json.loads(base64.b64decode(result["raw_response"]["body_base64"]))
        ticket = body["ticket"]
        self.assertEqual(cog._resolve_ticket(ticket), 777)


class TestEditorTickets(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.cog = _make_cog()

    def test_minted_ticket_resolves_to_user(self):
        ticket = self.cog._mint_ticket(4242)
        self.assertEqual(self.cog._resolve_ticket(ticket), 4242)

    def test_unknown_ticket_resolves_to_none(self):
        self.assertIsNone(self.cog._resolve_ticket("nope"))

    def test_expired_ticket_is_rejected_and_dropped(self):
        now = [0.0]
        store = TicketStore(clock=lambda: now[0], token_factory=lambda: "ticket")
        ticket = store.mint(1)
        now[0] = 8 * 60 * 60
        self.assertIsNone(store.resolve(ticket))
        self.assertNotIn(ticket, store.tickets)

    def test_minting_evicts_expired_tickets(self):
        now = [0.0]
        tokens = iter(("stale", "fresh"))
        store = TicketStore(clock=lambda: now[0], token_factory=lambda: next(tokens))
        stale = store.mint(1)
        now[0] = 8 * 60 * 60
        store.mint(2)
        self.assertNotIn(stale, store.tickets)


class TestLayoutOwnership(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.cog = _make_cog()

    async def test_saved_layout_wins_over_bundled_default(self):
        layout = _valid_layout()
        await self.cog.config.layout.set(layout)
        self.cog._default_layout = lambda: {"version": 1, "cols": 9, "rows": 9,
                                            "tiles": [0] * 81, "furniture": []}
        self.assertEqual(await self.cog._current_layout(), layout)

    async def test_falls_back_to_bundled_default(self):
        default = _valid_layout()
        self.cog._default_layout = lambda: default
        self.assertEqual(await self.cog._current_layout(), default)


# ---------------------------------------------------------------------------
# Tests: listener routing
# ---------------------------------------------------------------------------

class TestMemberUpdateListener(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.cog = _make_enabled_cog()
        self.cog._reconcile_member = AsyncMock()

    async def test_name_change_reconciles(self):
        before = _member(display_name="Old")
        after = _member(display_name="New")
        await self.cog.on_member_update(before, after)
        self.cog._reconcile_member.assert_awaited_once()

    async def test_no_name_change_skips(self):
        before = _member(display_name="Same", status="online")
        after = _member(display_name="Same", status="dnd")
        await self.cog.on_member_update(before, after)
        self.cog._reconcile_member.assert_not_awaited()


class TestPresenceUpdateListener(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.cog = _make_enabled_cog()
        self.cog._reconcile_member = AsyncMock()

    async def test_status_change_reconciles(self):
        before = _member(status="online")
        after = _member(status="idle")
        await self.cog.on_presence_update(before, after)
        self.cog._reconcile_member.assert_awaited_once()

    async def test_activity_change_reconciles(self):
        before = _member(activities=[])
        after = _member(activities=[_activity(discord.ActivityType.playing)])
        await self.cog.on_presence_update(before, after)
        self.cog._reconcile_member.assert_awaited_once()

    async def test_no_change_skips(self):
        before = _member(status="online", activities=[])
        after = _member(status="online", activities=[])
        await self.cog.on_presence_update(before, after)
        self.cog._reconcile_member.assert_not_awaited()

    async def test_disabled_guild_skips(self):
        cog = _make_cog()
        cog._reconcile_member = AsyncMock()
        before = _member(status="online")
        after = _member(status="dnd")
        await cog.on_presence_update(before, after)
        cog._reconcile_member.assert_not_awaited()


class TestMemberJoinListener(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.cog = _make_enabled_cog()
        self.cog._reconcile_member = AsyncMock()

    async def test_visible_member_reconciles(self):
        m = _member(status="online")
        await self.cog.on_member_join(m)
        self.cog._reconcile_member.assert_awaited_once()

    async def test_offline_member_skips(self):
        m = _member(status="offline")
        await self.cog.on_member_join(m)
        self.cog._reconcile_member.assert_not_awaited()


class TestMemberRemoveListener(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.cog = _make_enabled_cog()
        self.cog._close_agent = AsyncMock()

    async def test_remove_calls_close(self):
        m = _member(guild_id=100, user_id=42)
        await self.cog.on_member_remove(m)
        self.cog._close_agent.assert_awaited_once_with(100, 42)


# ---------------------------------------------------------------------------
# Tests: on_message
# ---------------------------------------------------------------------------

class TestOnMessage(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.cog = _make_enabled_cog()
        self.ws = _connect(self.cog)

    async def test_message_sends_tool_start(self):
        self.cog._agents[(100, 1)] = ("online", "Tin")
        msg = MagicMock()
        msg.guild.id = 100
        msg.author.id = 1
        msg.content = "Hello world"
        msg.id = 999
        await self.cog.on_message(msg)
        sent_types = [json.loads(s)["type"] for s in self.ws._sent]
        self.assertIn("agentToolStart", sent_types)

    async def test_message_selects_the_agent(self):
        """agentSelected must accompany the Message tool bubble so the label
        panel (with the message text) is visible immediately, without hover
        or "Always Show Labels"."""
        self.cog._agents[(100, 1)] = ("online", "Tin")
        msg = MagicMock()
        msg.guild.id = 100
        msg.author.id = 1
        msg.content = "Hello world"
        msg.id = 999
        await self.cog.on_message(msg)
        sent = [json.loads(s) for s in self.ws._sent]
        self.assertEqual([m["type"] for m in sent], ["agentToolStart", "agentSelected"])
        self.assertEqual(sent[1]["id"], self.cog._office_service.agent_id(1))

    async def test_message_truncates_long_content(self):
        self.cog._agents[(100, 1)] = ("online", "Tin")
        msg = MagicMock()
        msg.guild.id = 100
        msg.author.id = 1
        msg.content = "x" * 100
        msg.id = 1
        await self.cog.on_message(msg)
        tool_msg = next(json.loads(s) for s in self.ws._sent if json.loads(s)["type"] == "agentToolStart")
        self.assertLessEqual(len(tool_msg["status"]), 45)

    async def test_message_clear_does_not_reping(self):
        """After the message-clear delay, only agentToolsClear is sent —
        agentSelected (sent at message-start) already made the message
        visible without needing hover, so clearing must not also emit a
        status ping."""
        self.cog._agents[(100, 1)] = ("online", "Tin")
        msg = MagicMock()
        msg.guild.id = 100
        msg.author.id = 1
        msg.content = "Hello world"
        msg.id = 999
        await self.cog.on_message(msg)
        self.ws._sent.clear()

        await self.cog._clear_tool_after_delay(
            self.cog._office_service.agent_id(1), 0, guild_id=100, user_id=1
        )

        sent_types = [json.loads(s)["type"] for s in self.ws._sent]
        self.assertEqual(sent_types, ["agentToolsClear"])

    async def test_message_ignored_if_not_tracked(self):
        msg = MagicMock()
        msg.guild.id = 100
        msg.author.id = 999
        msg.content = "hi"
        await self.cog.on_message(msg)
        self.assertEqual(len(self.ws._sent), 0)

    async def test_message_ignored_in_dm(self):
        msg = MagicMock()
        msg.guild = None
        await self.cog.on_message(msg)
        self.assertEqual(len(self.ws._sent), 0)


# ---------------------------------------------------------------------------
# Tests: commands
# ---------------------------------------------------------------------------

class TestToolClearDelayCommand(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.cog = _make_cog()

    async def test_set_valid_delay(self):
        ctx = MagicMock()
        ctx.interaction = None
        ctx.send = AsyncMock()
        await self.cog.cmd_toolcleardelay(ctx, 5.0)
        self.assertEqual(await self.cog.config.message_tool_clear_delay(), 5.0)
        ctx.send.assert_awaited_once()
        self.assertIn("5.0", ctx.send.call_args.kwargs["content"])

    async def test_negative_delay_rejected(self):
        ctx = MagicMock()
        ctx.interaction = None
        ctx.send = AsyncMock()
        await self.cog.cmd_toolcleardelay(ctx, -1.0)
        self.assertEqual(await self.cog.config.message_tool_clear_delay(), 2.0)


class TestWsPortCommand(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.cog = _make_cog()

    def _ctx(self):
        ctx = MagicMock()
        ctx.interaction = None
        ctx.send = AsyncMock()
        return ctx

    async def test_sets_port(self):
        await self.cog.cmd_wsport(self._ctx(), 4300)
        self.assertEqual(await self.cog.config.ws_port(), 4300)

    async def test_rejects_out_of_range_port(self):
        await self.cog.cmd_wsport(self._ctx(), 70000)
        self.assertEqual(await self.cog.config.ws_port(), 3210)


class _FakeHttpResponse:
    def __init__(self, status=200, payload=None):
        self.status = status
        self._payload = payload

    async def json(self):
        return self._payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


class _FakeHttpSession:
    def __init__(self, response=None, exc=None):
        self._response = response
        self._exc = exc
        self.closed = False
        self.last_url = None
        self.last_params = None

    def get(self, url, params=None):
        self.last_url = url
        self.last_params = params
        if self._exc:
            raise self._exc
        return self._response

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def close(self):
        self.closed = True


def _layout_summary(slug="office", title="Office", **overrides):
    entry = {
        "slug": slug,
        "title": title,
        "author": {"discordId": "1", "username": "tin", "displayName": "Tin", "avatarUrl": None},
        "description": "A tidy office",
        "tags": ["cozy"],
        "cols": 10,
        "rows": 10,
        "visibleCols": 8,
        "visibleRows": 8,
        "furniture": 3,
        "areas": 1,
        "pets": 0,
        "carpets": 1,
        "seats": 2,
        "layoutRevision": 1,
        "pixelAgentsVersion": "1.4.0",
        "bytes": 1234,
        "sha256": "a" * 64,
        "createdAt": "2026-01-01T00:00:00.000Z",
        "updatedAt": "2026-01-01T00:00:00.000Z",
        "files": {
            "layout": f"/api/v1/layouts/{slug}/download",
            "preview": f"/api/v1/layouts/{slug}/preview.png",
            "thumbnail": f"/api/v1/layouts/{slug}/thumbnail.png",
        },
    }
    entry.update(overrides)
    return entry


def _layout_detail(slug="office", **overrides):
    detail = _layout_summary(slug=slug)
    detail["layout"] = {"version": 1, "cols": 2, "rows": 2, "tiles": [1, 1, 1, 1], "furniture": []}
    detail.update(overrides)
    return detail


class TestCleanUrl(unittest.TestCase):
    def test_accepts_valid_https_url(self):
        self.assertEqual(FloorplanCog._clean_url("https://example.com/"), "https://example.com")

    def test_rejects_missing_scheme(self):
        self.assertIsNone(FloorplanCog._clean_url("example.com"))

    def test_rejects_non_http_scheme(self):
        self.assertIsNone(FloorplanCog._clean_url("ftp://example.com"))


class TestPixelIndexSetwebCommand(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.cog = _make_cog()

    def _ctx(self):
        ctx = MagicMock()
        ctx.interaction = None
        ctx.send = AsyncMock()
        return ctx

    async def test_sets_web_url(self):
        await self.cog.cmd_pixelindex_setweb(self._ctx(), "https://pixel-index.vercel.app/")
        self.assertEqual(await self.cog.config.pixel_index_web_url(), "https://pixel-index.vercel.app")

    async def test_rejects_invalid_url(self):
        ctx = self._ctx()
        await self.cog.cmd_pixelindex_setweb(ctx, "not-a-url")
        self.assertIn("valid URL", ctx.send.call_args.kwargs["content"])


class TestPixelIndexGet(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.cog = _make_cog()

    async def test_success_returns_json(self):
        session = _FakeHttpSession(response=_FakeHttpResponse(200, {"ok": True}))
        with patch.object(aiohttp, "ClientSession", return_value=session):
            ok, data = await self.cog._pixel_index_get("/api/v1/meta")
        self.assertTrue(ok)
        self.assertEqual(data, {"ok": True})
        self.assertTrue(session.last_url.endswith("/api/v1/meta"))

    async def test_non_200_status_is_reported(self):
        session = _FakeHttpSession(response=_FakeHttpResponse(500, None))
        with patch.object(aiohttp, "ClientSession", return_value=session):
            ok, data = await self.cog._pixel_index_get("/api/v1/meta")
        self.assertFalse(ok)
        self.assertIn("500", data)

    async def test_connection_error_is_reported(self):
        session = _FakeHttpSession(exc=OSError("boom"))
        with patch.object(aiohttp, "ClientSession", return_value=session):
            ok, data = await self.cog._pixel_index_get("/api/v1/meta")
        self.assertFalse(ok)
        self.assertIn("boom", data)

    async def test_search_builds_expected_params(self):
        session = _FakeHttpSession(response=_FakeHttpResponse(200, {"layouts": []}))
        with patch.object(aiohttp, "ClientSession", return_value=session):
            await self.cog._pixel_index_search(query="cozy", tag="pets", sort="furniture", cursor="abc")
        self.assertEqual(
            session.last_params,
            {"sort": "furniture", "limit": 5, "q": "cozy", "tags": "pets", "cursor": "abc"},
        )

    async def test_search_accepts_well_shaped_response(self):
        page = {"layouts": [_layout_summary("office")], "total": 1, "nextCursor": None}
        session = _FakeHttpSession(response=_FakeHttpResponse(200, page))
        with patch.object(aiohttp, "ClientSession", return_value=session):
            ok, data = await self.cog._pixel_index_search(query=None, tag=None, sort="newest")
        self.assertTrue(ok)
        self.assertEqual(data, LayoutListResponse.model_validate(page))

    async def test_search_rejects_response_missing_slug(self):
        page = {"layouts": [{"title": "Office"}], "total": 1}
        session = _FakeHttpSession(response=_FakeHttpResponse(200, page))
        with patch.object(aiohttp, "ClientSession", return_value=session):
            ok, message = await self.cog._pixel_index_search(query=None, tag=None, sort="newest")
        self.assertFalse(ok)
        self.assertIn("unexpected response", message)

    async def test_layout_accepts_well_shaped_response(self):
        detail = _layout_detail("office")
        session = _FakeHttpSession(response=_FakeHttpResponse(200, detail))
        with patch.object(aiohttp, "ClientSession", return_value=session):
            ok, data = await self.cog._pixel_index_layout("office")
        self.assertTrue(ok)
        self.assertEqual(data, LayoutDetail.model_validate(detail))

    async def test_layout_rejects_response_missing_layout_blob(self):
        detail = _layout_detail("office")
        del detail["layout"]
        session = _FakeHttpSession(response=_FakeHttpResponse(200, detail))
        with patch.object(aiohttp, "ClientSession", return_value=session):
            ok, message = await self.cog._pixel_index_layout("office")
        self.assertFalse(ok)
        self.assertIn("unexpected response", message)


class TestLoadPixelIndexLayout(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.cog = _make_cog()

    async def test_rejects_unauthorized_user(self):
        self.cog.bot.is_owner = AsyncMock(return_value=False)
        ok, message = await self.cog._load_pixel_index_layout(12345, "office")
        self.assertFalse(ok)
        self.assertIn("not authorized", message)

    async def test_rejects_invalid_layout(self):
        self.cog.bot.is_owner = AsyncMock(return_value=True)
        detail = _layout_detail("office")
        detail["layout"] = {"not": "valid"}
        self.cog._catalogue_service.detail = AsyncMock(
            return_value=CatalogueResult(value=LayoutDetail.model_validate(detail))
        )
        ok, message = await self.cog._load_pixel_index_layout(12345, "office")
        self.assertFalse(ok)
        self.assertIn("invalid", message)

    async def test_loads_valid_layout_and_broadcasts(self):
        self.cog.bot.is_owner = AsyncMock(return_value=True)
        detail = _layout_detail("office")
        model = LayoutDetail.model_validate(detail)
        self.cog._catalogue_service.detail = AsyncMock(
            return_value=CatalogueResult(value=model)
        )
        client = _connect(self.cog)

        ok, message = await self.cog._load_pixel_index_layout(12345, "office")

        self.assertTrue(ok)
        self.assertEqual(await self.cog.config.layout(), model.layout)
        self.assertIn("layoutLoaded", _sent_types(client))


class TestLayoutBrowseView(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.cog = _make_cog()
        self.page = LayoutListResponse.model_validate({
            "total": 1,
            "layouts": [_layout_summary("office", "Office")],
            "nextCursor": "next-cursor",
        })

    def _view(self, page_index=0, pages=None):
        return _LayoutBrowseView(
            self.cog._catalogue_service,
            owner_id=1,
            query=None,
            tag=None,
            sort="newest",
            pages=pages or [self.page],
            page_index=page_index,
            api_base="https://pixel-index-api-staging.nntin.xyz",
            web_base="https://pixel-index.vercel.app",
        )

    def test_builds_without_error(self):
        view = self._view()
        self.assertEqual(view.page_index, 0)

    async def test_next_fetches_and_advances(self):
        view = self._view()
        second_page = LayoutListResponse.model_validate(
            {"total": 1, "layouts": [_layout_summary("second")], "nextCursor": None}
        )
        self.cog._catalogue_service.search = AsyncMock(
            return_value=CatalogueResult(value=second_page)
        )
        interaction = _FakeInteraction()
        interaction.response.edit_message = AsyncMock()

        await view._on_next(interaction)

        self.cog._catalogue_service.search.assert_awaited_with(
            query=None, tag=None, sort="newest", cursor="next-cursor"
        )
        interaction.response.edit_message.assert_awaited()
        new_view = interaction.response.edit_message.call_args.kwargs["view"]
        self.assertEqual(new_view.page_index, 1)

    async def test_prev_at_first_page_defers(self):
        view = self._view()
        interaction = _FakeInteraction()
        interaction.response.defer = AsyncMock()

        await view._on_prev(interaction)

        interaction.response.defer.assert_awaited()

    async def test_prev_returns_to_cached_page(self):
        first_page = self.page.model_copy()
        second_page = LayoutListResponse.model_validate(
            {"total": 1, "layouts": [_layout_summary("second")], "nextCursor": None}
        )
        view = self._view(page_index=1, pages=[first_page, second_page])
        interaction = _FakeInteraction()
        interaction.response.edit_message = AsyncMock()

        await view._on_prev(interaction)

        new_view = interaction.response.edit_message.call_args.kwargs["view"]
        self.assertEqual(new_view.page_index, 0)


class TestLayoutDetailView(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.cog = _make_cog()
        self.detail = LayoutDetail.model_validate(_layout_detail("office", title="Office"))

    def _view(self, back=None):
        return _LayoutDetailView(
            self.cog._catalogue_service,
            owner_id=1,
            detail=self.detail,
            api_base="https://pixel-index-api-staging.nntin.xyz",
            web_base="https://pixel-index.vercel.app",
            back=back,
        )

    def test_builds_without_error(self):
        view = self._view()
        self.assertEqual(view.detail.slug, "office")

    async def test_load_button_delegates_to_catalogue_service(self):
        view = self._view()
        self.cog._catalogue_service.load_layout = AsyncMock(
            return_value=CatalogueResult(value="Loaded `Office` into the office.")
        )
        interaction = _FakeInteraction()
        interaction.response.send_message = AsyncMock()

        await view._on_load(interaction)

        self.cog._catalogue_service.load_layout.assert_awaited_with(
            interaction.user.id, "office"
        )
        interaction.response.send_message.assert_awaited_with(
            "Loaded `Office` into the office.", ephemeral=True
        )

    async def test_back_button_edits_to_browse_view(self):
        browse_view = MagicMock()
        view = self._view(back=browse_view)
        interaction = _FakeInteraction()
        interaction.response.edit_message = AsyncMock()

        await view._on_back(interaction)

        interaction.response.edit_message.assert_awaited_with(view=browse_view)


class TestReplyHelper(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.cog = _make_cog()

    async def test_prefix_uses_ctx_send(self):
        ctx = MagicMock()
        ctx.interaction = None
        ctx.send = AsyncMock()
        await self.cog._reply(ctx, "hello")
        ctx.send.assert_awaited_once_with(content="hello")

    async def test_text_mode_renders_through_corridor(self):
        self.cog._corridor = FakeCorridor(reply_mode="text")
        ctx = MagicMock()
        ctx.interaction = None
        ctx.send = AsyncMock()
        await self.cog._reply(ctx, "hello", title="Pixel Agents")
        ctx.send.assert_awaited_once_with(content="hello")
        self.assertEqual(
            self.cog._corridor.rendered_replies,
            [(ctx.guild.id, "Pixel Agents", "hello", None, ())],
        )

    async def test_embed_mode_renders_through_corridor(self):
        self.cog._corridor = FakeCorridor(reply_mode="embed")
        ctx = MagicMock()
        ctx.interaction = None
        ctx.send = AsyncMock()
        await self.cog._reply(ctx, "hello", title="Pixel Agents")
        ctx.send.assert_awaited_once()
        self.assertIn("embed", ctx.send.call_args.kwargs)
        self.assertNotIn("content", ctx.send.call_args.kwargs)

    async def test_embed_mode_reply_carries_fields(self):
        self.cog._corridor = FakeCorridor(reply_mode="embed")
        ctx = MagicMock()
        ctx.interaction = None
        ctx.send = AsyncMock()
        fields = [ReplyField("Serving", "yes", False), ReplyField("Clients", "3")]

        await self.cog._reply(ctx, title="Status", fields=fields)

        embed = ctx.send.call_args.kwargs["embed"]
        self.assertEqual(
            [call.kwargs for call in embed.add_field.call_args_list],
            [
                {"name": "Serving", "value": "yes", "inline": False},
                {"name": "Clients", "value": "3", "inline": True},
            ],
        )

    async def test_text_mode_reply_flattens_fields_to_lines(self):
        self.cog._corridor = FakeCorridor(reply_mode="text")
        ctx = MagicMock()
        ctx.interaction = None
        ctx.send = AsyncMock()
        fields = [ReplyField("Serving", "yes"), ReplyField("Clients", "3")]

        await self.cog._reply(ctx, title="Status", fields=fields)

        ctx.send.assert_awaited_once_with(
            content="Status\n**Serving:** yes\n**Clients:** 3"
        )

    async def test_view_only_reply_bypasses_corridor(self):
        self.cog._corridor = FakeCorridor(reply_mode="embed")
        ctx = MagicMock()
        ctx.interaction = None
        ctx.send = AsyncMock()
        view = object()
        await self.cog._reply(ctx, view=view)
        ctx.send.assert_awaited_once_with(view=view)
        self.assertEqual(self.cog._corridor.rendered_replies, [])

    async def test_slash_uses_response_send_message(self):
        ctx = MagicMock()
        interaction = _FakeInteraction(guild=MagicMock())
        ctx.interaction = interaction
        sent = []

        async def _capture(*args, **kwargs):
            sent.append((args, kwargs))
            interaction.response._done = True

        interaction.response.send_message = _capture
        await self.cog._reply(ctx, "hello")
        self.assertEqual(len(sent), 1)
        _, kwargs = sent[0]
        self.assertTrue(kwargs.get("ephemeral"))

    async def test_slash_after_defer_uses_followup(self):
        ctx = MagicMock()
        interaction = _FakeInteraction(guild=MagicMock())
        interaction.response._done = True
        ctx.interaction = interaction
        sent = []

        async def _capture(*args, **kwargs):
            sent.append((args, kwargs))

        interaction.followup.send = _capture
        await self.cog._reply(ctx, "after defer")
        self.assertEqual(len(sent), 1)
        _, kwargs = sent[0]
        self.assertTrue(kwargs.get("ephemeral"))


if __name__ == "__main__":
    unittest.main()
