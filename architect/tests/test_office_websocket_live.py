"""Live round trip against a real, loopback-bound architect office
WebSocket server -- this is the actual bug this whole feature exists to
fix: architect's webview must render *its own* stored layout over *its
own* live connection, never floorplan's (see docs/architect-design.md's
incident note on the vendored bundle's page-path-independent `/ws` URL).
Not mocked, same testing convention as pico's live A2A round trip
(pico/tests/test_architect_client.py) and architect's own A2A server
tests."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import aiohttp

from pixelagents.application.office import to_genuine_agent_id

from ..architect import Architect
from .conftest import FakeBot, FakePixelAgents, FakeUser

_PORT = 8942


def _write_bundle_with_default_layout(root: Path, *, tiles: list[int]) -> None:
    (root / "index.html").write_text("<!doctype html>", encoding="utf-8")
    (root / "assets").mkdir()
    (root / "assets" / "asset-index.json").write_text(
        json.dumps({"defaultLayout": "default-layout.json"}), encoding="utf-8"
    )
    (root / "assets" / "default-layout.json").write_text(
        json.dumps({"tiles": tiles}), encoding="utf-8"
    )


class TestOfficeWebSocketLiveRoundTrip(unittest.IsolatedAsyncioTestCase):
    async def test_webview_ready_returns_architects_own_seeded_layout(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_bundle_with_default_layout(root, tiles=[7, 8, 9])
            bot = FakeBot(pixelagents=FakePixelAgents(dist_path=root))
            cog = Architect(bot=bot)
            await cog.cog_load()
            self.addAsyncCleanup(cog.cog_unload)
            await cog._sync_webview_assets()  # type: ignore[attr-defined]  # seeds the layout
            # cog_load() already bound the default ws_port -- rebind onto
            # this test's own port directly against the server instance
            # (WebSocketServer.start() is a no-op once already running,
            # matching floorplan's own "reload to rebind" convention; see
            # adapters/commands.py's ws_host/ws_port docstrings).
            await cog._websocket_server.stop()  # type: ignore[attr-defined]
            await cog._websocket_server.start("127.0.0.1", _PORT)  # type: ignore[attr-defined]

            async with (
                aiohttp.ClientSession() as session,
                session.ws_connect(f"http://127.0.0.1:{_PORT}/architect/ws") as socket,
            ):
                await socket.send_str(json.dumps({"type": "webviewReady"}))

                layout_message = None
                async for raw in socket:
                    message = json.loads(raw.data)
                    if message.get("type") == "layoutLoaded":
                        layout_message = message
                        break

            assert layout_message is not None
            self.assertEqual(layout_message["layout"], {"tiles": [7, 8, 9]})

    async def test_two_connections_each_get_the_current_layout_independently(self) -> None:
        """Regression shape for the actual reported bug: two separate
        browser tabs (here, two separate socket connections) must each
        see the same architect-owned layout -- and, implicitly, neither
        this test nor the server code path involves floorplan at all."""

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_bundle_with_default_layout(root, tiles=[1])
            bot = FakeBot(pixelagents=FakePixelAgents(dist_path=root))
            cog = Architect(bot=bot)
            await cog.cog_load()
            self.addAsyncCleanup(cog.cog_unload)
            await cog._sync_webview_assets()  # type: ignore[attr-defined]
            await cog._websocket_server.stop()  # type: ignore[attr-defined]
            await cog._websocket_server.start("127.0.0.1", _PORT + 1)  # type: ignore[attr-defined]

            async def fetch_layout() -> object:
                async with (
                    aiohttp.ClientSession() as session,
                    session.ws_connect(f"http://127.0.0.1:{_PORT + 1}/architect/ws") as socket,
                ):
                    await socket.send_str(json.dumps({"type": "webviewReady"}))
                    async for raw in socket:
                        message = json.loads(raw.data)
                        if message.get("type") == "layoutLoaded":
                            return message["layout"]
                return None

            first = await fetch_layout()
            second = await fetch_layout()

            self.assertEqual(first, {"tiles": [1]})
            self.assertEqual(second, {"tiles": [1]})

    async def test_save_layout_persists_and_broadcasts_with_no_authorization(self) -> None:
        """The actual feature this exists for: unlike floorplan's editor,
        no ticket/session/authorization handshake happens here at all --
        any connected socket can send `saveLayout` and have it both
        persisted and broadcast to every other connected client."""

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_bundle_with_default_layout(root, tiles=[7, 8, 9])
            bot = FakeBot(pixelagents=FakePixelAgents(dist_path=root))
            cog = Architect(bot=bot)
            await cog.cog_load()
            self.addAsyncCleanup(cog.cog_unload)
            await cog._sync_webview_assets()  # type: ignore[attr-defined]
            await cog._websocket_server.stop()  # type: ignore[attr-defined]
            await cog._websocket_server.start("127.0.0.1", _PORT + 2)  # type: ignore[attr-defined]

            new_layout = {"version": 1, "cols": 2, "rows": 1, "tiles": [1, 1], "furniture": []}

            async def next_layout_loaded(socket: aiohttp.ClientWebSocketResponse) -> object:
                async for raw in socket:
                    message = json.loads(raw.data)
                    if message.get("type") == "layoutLoaded":
                        return message["layout"]
                return None

            async with (
                aiohttp.ClientSession() as session,
                session.ws_connect(f"http://127.0.0.1:{_PORT + 2}/architect/ws") as editor,
                session.ws_connect(f"http://127.0.0.1:{_PORT + 2}/architect/ws") as viewer,
            ):
                # No `authorize`/ticket message sent by either socket --
                # the save still succeeds.
                await editor.send_str(json.dumps({"type": "saveLayout", "layout": new_layout}))

                editor_saw = await next_layout_loaded(editor)
                viewer_saw = await next_layout_loaded(viewer)

            # encode() adds its own `tileColors` (all-`None`, since this
            # payload never set any) -- compare the fields the browser
            # actually sent, not encode()'s own added-back fields.
            assert isinstance(editor_saw, dict)
            self.assertEqual(editor_saw["cols"], 2)
            self.assertEqual(editor_saw["tiles"], [1, 1])
            self.assertEqual(editor_saw, viewer_saw)
            persisted = await cog._repository.layout()  # type: ignore[attr-defined]
            self.assertEqual(persisted, editor_saw)

    async def test_invalid_save_layout_is_dropped_without_persisting_or_crashing(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_bundle_with_default_layout(root, tiles=[7, 8, 9])
            bot = FakeBot(pixelagents=FakePixelAgents(dist_path=root))
            cog = Architect(bot=bot)
            await cog.cog_load()
            self.addAsyncCleanup(cog.cog_unload)
            await cog._sync_webview_assets()  # type: ignore[attr-defined]
            await cog._websocket_server.stop()  # type: ignore[attr-defined]
            await cog._websocket_server.start("127.0.0.1", _PORT + 3)  # type: ignore[attr-defined]

            before = await cog._repository.layout()  # type: ignore[attr-defined]

            async with (
                aiohttp.ClientSession() as session,
                session.ws_connect(f"http://127.0.0.1:{_PORT + 3}/architect/ws") as socket,
            ):
                # Missing "cols"/"rows"/"tiles" entirely -- decode() itself
                # raises; the connection must survive it and keep serving.
                await socket.send_str(
                    json.dumps({"type": "saveLayout", "layout": {"furniture": []}})
                )
                await socket.send_str(json.dumps({"type": "webviewReady"}))

                layout_message = None
                async for raw in socket:
                    message = json.loads(raw.data)
                    if message.get("type") == "layoutLoaded":
                        layout_message = message
                        break

            assert layout_message is not None
            self.assertEqual(layout_message["layout"], before)

    async def test_bootstrap_includes_architects_own_agent_and_bot_account(self) -> None:
        """The actual end-to-end proof the reported symptom ("architect's
        dashboard shows no agents") is fixed: a real browser connection's
        bootstrap sequence includes architect's own genuine-agent ID (from
        its self-registration with corridor) and its own Discord bot
        account's synthetic ID -- not just that the application-layer
        roster dict has entries."""

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_bundle_with_default_layout(root, tiles=[7, 8, 9])
            bot = FakeBot(pixelagents=FakePixelAgents(dist_path=root), user=FakeUser(user_id=42))
            cog = Architect(bot=bot)
            await cog.cog_load()
            self.addAsyncCleanup(cog.cog_unload)
            await cog._sync_webview_assets()  # type: ignore[attr-defined]
            await cog._websocket_server.stop()  # type: ignore[attr-defined]
            await cog._websocket_server.start("127.0.0.1", _PORT + 4)  # type: ignore[attr-defined]

            async with (
                aiohttp.ClientSession() as session,
                session.ws_connect(f"http://127.0.0.1:{_PORT + 4}/architect/ws") as socket,
            ):
                await socket.send_str(json.dumps({"type": "webviewReady"}))

                existing_agents_message = None
                async for raw in socket:
                    message = json.loads(raw.data)
                    if message.get("type") == "existingAgents":
                        existing_agents_message = message
                        break

            assert existing_agents_message is not None
            agent_ids = set(existing_agents_message["agents"])
            self.assertIn(to_genuine_agent_id("architect"), agent_ids)
            self.assertIn(to_genuine_agent_id("discord-bot-42"), agent_ids)
