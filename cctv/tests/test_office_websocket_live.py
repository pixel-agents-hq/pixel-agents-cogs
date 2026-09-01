"""Live round trip against a real, loopback-bound cctv office server --
both pipelines, on one shared listener, each with its own auth policy.
Not mocked, same convention architect's own former
`test_office_websocket_live.py` (now retired there) already established.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import aiohttp

from ..cctv import Cctv
from .conftest import FakeBot, FakeCorridor, FakeGuild, FakeMember, FakePixelAgents

_PORT = 8952

_DEFAULT_LAYOUT = {"version": 1, "cols": 1, "rows": 1, "tiles": [1], "furniture": []}


def _write_bundle(root: Path) -> None:
    (root / "index.html").write_text("<!doctype html><head></head><body></body>", encoding="utf-8")
    (root / "assets").mkdir()
    (root / "assets" / "asset-index.json").write_text(
        json.dumps({"defaultLayout": "default-layout.json"}), encoding="utf-8"
    )
    (root / "assets" / "default-layout.json").write_text(
        json.dumps(_DEFAULT_LAYOUT), encoding="utf-8"
    )


async def _next_layout_loaded(socket: aiohttp.ClientWebSocketResponse) -> object:
    async for raw in socket:
        message = json.loads(raw.data)
        if message.get("type") == "layoutLoaded":
            return message["layout"]
    return None


async def _next_existing_agents(socket: aiohttp.ClientWebSocketResponse) -> dict[str, object]:
    async for raw in socket:
        message = json.loads(raw.data)
        if message.get("type") == "existingAgents":
            return message
    return {}


class _LiveTestBase(unittest.IsolatedAsyncioTestCase):
    async def _start(self, *, port: int, corridor: FakeCorridor | None = None) -> Cctv:
        self._tmp = TemporaryDirectory()
        root = Path(self._tmp.name)
        _write_bundle(root)
        corridor = corridor or FakeCorridor()
        pixelagents = FakePixelAgents(corridor=corridor, dist_path=root)
        bot = FakeBot(corridor=corridor, pixelagents=pixelagents)
        cog = Cctv(bot=bot)
        await cog.cog_load()
        self.addAsyncCleanup(cog.cog_unload)
        self.addCleanup(self._tmp.cleanup)
        await cog._sync_webview_assets()
        await cog._websocket_server.stop()
        await cog._websocket_server.start("127.0.0.1", port)
        return cog


class TestDiscordPipeline(_LiveTestBase):
    async def test_webview_ready_returns_the_seeded_default_layout(self) -> None:
        await self._start(port=_PORT)

        async with (
            aiohttp.ClientSession() as session,
            session.ws_connect(f"http://127.0.0.1:{_PORT}/cctv/discord/ws") as socket,
        ):
            await socket.send_str(json.dumps({"type": "webviewReady"}))
            layout = await _next_layout_loaded(socket)

        self.assertEqual(layout, _DEFAULT_LAYOUT)

    async def test_save_layout_is_dropped_without_authorization(self) -> None:
        await self._start(port=_PORT + 1)
        new_layout = {"version": 1, "cols": 2, "rows": 1, "tiles": [1, 1], "furniture": []}

        async with (
            aiohttp.ClientSession() as session,
            session.ws_connect(f"http://127.0.0.1:{_PORT + 1}/cctv/discord/ws") as socket,
        ):
            await socket.send_str(json.dumps({"type": "saveLayout", "layout": new_layout}))
            await socket.send_str(json.dumps({"type": "webviewReady"}))
            layout = await _next_layout_loaded(socket)

        # Unauthorized write silently dropped -- webviewReady still reports
        # the original seeded layout, not the rejected write.
        self.assertEqual(layout, _DEFAULT_LAYOUT)

    async def test_save_layout_succeeds_after_ticket_authorization(self) -> None:
        guild = FakeGuild(1)
        member = FakeMember(7, guild=guild)
        guild.add_member(member)
        corridor = FakeCorridor(keyholders=frozenset({7}))
        cog = await self._start(port=_PORT + 2, corridor=corridor)
        await cog._repository.set_guild_enabled(guild.id, True)
        cog.bot.guilds = [guild]
        ticket = cog._tickets.mint(7)
        new_layout = {"version": 1, "cols": 2, "rows": 1, "tiles": [1, 1], "furniture": []}

        async with (
            aiohttp.ClientSession() as session,
            session.ws_connect(
                f"http://127.0.0.1:{_PORT + 2}/cctv/discord/ws?ticket={ticket}"
            ) as socket,
        ):
            await socket.send_str(json.dumps({"type": "saveLayout", "layout": new_layout}))
            await socket.send_str(json.dumps({"type": "webviewReady"}))
            layout = await _next_layout_loaded(socket)

        assert isinstance(layout, dict)
        self.assertEqual(layout["cols"], 2)
        self.assertEqual(layout["tiles"], [1, 1])


class TestEditorPipeline(_LiveTestBase):
    async def test_webview_ready_returns_the_seeded_default_layout(self) -> None:
        await self._start(port=_PORT + 3)

        async with (
            aiohttp.ClientSession() as session,
            session.ws_connect(f"http://127.0.0.1:{_PORT + 3}/cctv/editor/ws") as socket,
        ):
            await socket.send_str(json.dumps({"type": "webviewReady"}))
            layout = await _next_layout_loaded(socket)

        assert isinstance(layout, dict)
        self.assertEqual(layout["cols"], 1)
        self.assertEqual(layout["rows"], 1)

    async def test_save_layout_persists_and_broadcasts_with_no_authorization(self) -> None:
        """The whole point of the editor pipeline: unlike the Discord page,
        no ticket/session handshake happens at all -- any connected socket
        can save, and every other connected socket sees the update."""

        await self._start(port=_PORT + 4)
        new_layout = {"cols": 2, "rows": 1, "tiles": [1, 1], "furniture": []}

        async with (
            aiohttp.ClientSession() as session,
            session.ws_connect(f"http://127.0.0.1:{_PORT + 4}/cctv/editor/ws") as editor,
            session.ws_connect(f"http://127.0.0.1:{_PORT + 4}/cctv/editor/ws") as viewer,
        ):
            await editor.send_str(json.dumps({"type": "saveLayout", "layout": new_layout}))

            editor_saw = await _next_layout_loaded(editor)
            viewer_saw = await _next_layout_loaded(viewer)

        assert isinstance(editor_saw, dict)
        self.assertEqual(editor_saw["cols"], 2)
        self.assertEqual(editor_saw, viewer_saw)

    async def test_invalid_save_layout_is_dropped_without_persisting_or_crashing(self) -> None:
        await self._start(port=_PORT + 5)

        async with (
            aiohttp.ClientSession() as session,
            session.ws_connect(f"http://127.0.0.1:{_PORT + 5}/cctv/editor/ws") as socket,
        ):
            # Missing "cols"/"rows"/"tiles" entirely -- decode() itself
            # raises; the connection must survive it and keep serving.
            await socket.send_str(json.dumps({"type": "saveLayout", "layout": {"furniture": []}}))
            await socket.send_str(json.dumps({"type": "webviewReady"}))
            layout = await _next_layout_loaded(socket)

        self.assertEqual(layout, _DEFAULT_LAYOUT)


class TestSeatSavesBroadcastLive(_LiveTestBase):
    """docs/cctv-design.md's "broadcast layout/seat effects" -- a
    saveAgentSeats write publishes OfficeStateChanged exactly like a
    layout write, and that live-delivery path must carry the updated
    seat metadata to already-connected tabs, not just the layout."""

    async def test_editor_seat_save_reaches_another_connected_tab(self) -> None:
        from pixelagents.domain import GenuineAgentKey

        cog = await self._start(port=_PORT + 7)
        # existingAgents only ever reports seat metadata for a currently
        # tracked agent -- seed one the same way a real A2A registration
        # would, via the office service directly (that reconciliation
        # itself is event_subscriptions_editor.py's job, not under test
        # here).
        await cog._editor_office_service.reconcile_genuine_agent(
            GenuineAgentKey(agent_key="architect"), "architect", "online"
        )
        agent_id = str(cog._editor_office_service.genuine_agent_id("architect"))

        async with (
            aiohttp.ClientSession() as session,
            session.ws_connect(f"http://127.0.0.1:{_PORT + 7}/cctv/editor/ws") as editor,
            session.ws_connect(f"http://127.0.0.1:{_PORT + 7}/cctv/editor/ws") as viewer,
        ):
            await editor.send_str(
                json.dumps({"type": "saveAgentSeats", "seats": {agent_id: {"palette": 2}}})
            )
            existing = await _next_existing_agents(viewer)

        self.assertEqual(existing["agentMeta"][agent_id]["palette"], 2)

    async def test_multi_agent_seat_save_applies_every_patch_in_one_batch(self) -> None:
        from pixelagents.domain import GenuineAgentKey

        cog = await self._start(port=_PORT + 8)
        await cog._editor_office_service.reconcile_genuine_agent(
            GenuineAgentKey(agent_key="architect"), "architect", "online"
        )
        await cog._editor_office_service.reconcile_genuine_agent(
            GenuineAgentKey(agent_key="painter"), "painter", "online"
        )
        architect_id = str(cog._editor_office_service.genuine_agent_id("architect"))
        painter_id = str(cog._editor_office_service.genuine_agent_id("painter"))

        async with (
            aiohttp.ClientSession() as session,
            session.ws_connect(f"http://127.0.0.1:{_PORT + 8}/cctv/editor/ws") as editor,
        ):
            await editor.send_str(
                json.dumps(
                    {
                        "type": "saveAgentSeats",
                        "seats": {architect_id: {"palette": 1}, painter_id: {"palette": 2}},
                    }
                )
            )
            existing = await _next_existing_agents(editor)

        self.assertEqual(existing["agentMeta"][architect_id]["palette"], 1)
        self.assertEqual(existing["agentMeta"][painter_id]["palette"], 2)


class TestReauthorizeOnGuildDisable(_LiveTestBase):
    async def test_disabling_the_guild_revokes_an_already_authorized_sockets_write_access(
        self,
    ) -> None:
        guild = FakeGuild(1)
        member = FakeMember(7, guild=guild)
        guild.add_member(member)
        corridor = FakeCorridor(keyholders=frozenset({7}))
        cog = await self._start(port=_PORT + 9, corridor=corridor)
        await cog._repository.set_guild_enabled(guild.id, True)
        cog.bot.guilds = [guild]
        ticket = cog._tickets.mint(7)

        async with (
            aiohttp.ClientSession() as session,
            session.ws_connect(
                f"http://127.0.0.1:{_PORT + 9}/cctv/discord/ws?ticket={ticket}"
            ) as socket,
        ):
            # Confirm the socket really did authorize before disabling.
            still_editable_layout = {
                "version": 1,
                "cols": 2,
                "rows": 1,
                "tiles": [1, 1],
                "furniture": [],
            }
            await socket.send_str(
                json.dumps({"type": "saveLayout", "layout": still_editable_layout})
            )
            await socket.send_str(json.dumps({"type": "webviewReady"}))
            layout = await _next_layout_loaded(socket)
            assert isinstance(layout, dict)
            self.assertEqual(layout["cols"], 2)

            await cog.cmd_disable.callback(cog, _DisableContext(guild))

            rejected_layout = {
                "version": 1,
                "cols": 3,
                "rows": 1,
                "tiles": [1, 1, 1],
                "furniture": [],
            }
            await socket.send_str(json.dumps({"type": "saveLayout", "layout": rejected_layout}))
            await socket.send_str(json.dumps({"type": "webviewReady"}))
            layout_after = await _next_layout_loaded(socket)

        assert isinstance(layout_after, dict)
        self.assertEqual(layout_after["cols"], 2)  # unchanged -- the second save was dropped


class _DisableContext:
    """The narrow slice of `commands.Context` `cmd_disable` reads."""

    def __init__(self, guild: FakeGuild) -> None:
        self.guild = guild


class TestOnePipelineNeverLeaksIntoTheOther(_LiveTestBase):
    async def test_an_editor_save_never_touches_the_discord_page(self) -> None:
        await self._start(port=_PORT + 6)
        new_layout = {"cols": 3, "rows": 1, "tiles": [1, 1, 1], "furniture": []}

        async with (
            aiohttp.ClientSession() as session,
            session.ws_connect(f"http://127.0.0.1:{_PORT + 6}/cctv/editor/ws") as editor,
        ):
            await editor.send_str(json.dumps({"type": "saveLayout", "layout": new_layout}))
            await _next_layout_loaded(editor)

        async with (
            aiohttp.ClientSession() as session,
            session.ws_connect(f"http://127.0.0.1:{_PORT + 6}/cctv/discord/ws") as discord_socket,
        ):
            await discord_socket.send_str(json.dumps({"type": "webviewReady"}))
            layout = await _next_layout_loaded(discord_socket)

        self.assertEqual(layout, _DEFAULT_LAYOUT)


class TestDashboardPageShimOrder(unittest.IsolatedAsyncioTestCase):
    """The order the two injected `<script>` shims appear in is
    load-bearing (docs/cctv-design.md §2.4/§1.2): the WS-rewrite shim must
    render before the ticket shim, since the ticket shim captures
    `window.WebSocket` at its own injection time. A naive "both shims are
    present somewhere" assertion would pass even if the order regressed --
    this test checks the actual index order, not just presence."""

    async def _start(self) -> Cctv:
        self._tmp = TemporaryDirectory()
        root = Path(self._tmp.name)
        _write_bundle(root)
        corridor = FakeCorridor()
        pixelagents = FakePixelAgents(corridor=corridor, dist_path=root)
        bot = FakeBot(corridor=corridor, pixelagents=pixelagents)
        cog = Cctv(bot=bot)
        await cog.cog_load()
        self.addAsyncCleanup(cog.cog_unload)
        self.addCleanup(self._tmp.cleanup)
        return cog

    async def test_discord_page_has_both_shims_in_the_correct_order(self) -> None:
        cog = await self._start()

        response = await cog.dashboard_webview_discord()

        source = response["web_content"]["source"]  # type: ignore[index]
        ws_shim_index = source.index("/cctv/discord/ws")
        ticket_shim_index = source.index("location.pathname + '/session'")
        self.assertLess(ws_shim_index, ticket_shim_index)

    async def test_editor_page_has_only_the_ws_shim_no_ticket_shim(self) -> None:
        cog = await self._start()

        response = await cog.dashboard_webview_editor()

        source = response["web_content"]["source"]  # type: ignore[index]
        self.assertIn("/cctv/editor/ws", source)
        self.assertNotIn("location.pathname + '/session'", source)


if __name__ == "__main__":
    unittest.main()
