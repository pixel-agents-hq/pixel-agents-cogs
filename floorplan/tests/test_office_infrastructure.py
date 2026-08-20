"""Focused tests for the extracted office transport infrastructure."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, MagicMock, patch

from floorplan.floorplan import Floorplan as FloorplanCog
from floorplan.infrastructure.client_hub import ClientHub
from floorplan.infrastructure.tickets import TICKET_TTL_SECONDS, TicketStore
from floorplan.infrastructure.websocket import WebSocketServer
from floorplan.infrastructure.webview import WebviewAssetProvider
from floorplan.tests.conftest import _FakeClientWebSocketResponse, _FakeWSMessage


class TestTicketStore(unittest.TestCase):
    def test_tickets_are_reusable_until_monotonic_expiry(self) -> None:
        now = [100.0]
        store = TicketStore(clock=lambda: now[0], token_factory=lambda: "stable")

        ticket = store.mint(42)
        self.assertEqual(store.resolve(ticket), 42)
        now[0] += TICKET_TTL_SECONDS - 0.01
        self.assertEqual(store.resolve(ticket), 42)
        now[0] += 0.01
        self.assertIsNone(store.resolve(ticket))
        self.assertEqual(store.tickets, {})

    def test_mint_cleans_expired_tickets(self) -> None:
        now = [0.0]
        tokens = iter(("old", "new"))
        store = TicketStore(clock=lambda: now[0], token_factory=lambda: next(tokens))
        store.mint(1)
        now[0] = TICKET_TTL_SECONDS

        store.mint(2)

        self.assertEqual(set(store.tickets), {"new"})


class TestWebviewAssetProvider(unittest.TestCase):
    def test_asset_loading_filters_catalog_and_loads_default_layout(self) -> None:
        layout = {"version": 1, "cols": 1, "rows": 1, "tiles": [1], "furniture": []}
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            decoded = root / "assets" / "decoded"
            decoded.mkdir(parents=True)
            (decoded / "characters.json").write_text("[]", encoding="utf-8")
            (root / "assets" / "furniture-catalog.json").write_text(
                json.dumps([{"id": "DESK", "name": "Desk", "furniturePath": "private"}]),
                encoding="utf-8",
            )
            (root / "assets" / "asset-index.json").write_text(
                json.dumps({"defaultLayout": "default.json"}), encoding="utf-8"
            )
            (root / "assets" / "default.json").write_text(json.dumps(layout), encoding="utf-8")
            provider = WebviewAssetProvider(root)

            provider.load_assets()

            self.assertEqual(provider.assets["catalog"], [{"id": "DESK", "name": "Desk"}])
            self.assertEqual(provider.default_layout(), layout)
            self.assertIsNone(provider.resolve("../outside.txt"))


class TestClientHub(unittest.IsolatedAsyncioTestCase):
    async def test_broadcast_serializes_and_prunes_closed_clients(self) -> None:
        hub = ClientHub()
        live = _FakeClientWebSocketResponse()
        dead = _FakeClientWebSocketResponse()
        dead.closed = True
        hub.add(live)
        hub.add(dead)

        sent = await hub.broadcast({"type": "example", "nested": {"value": 1}})

        self.assertEqual(sent, 1)
        self.assertEqual(json.loads(live._sent[0])["nested"], {"value": 1})
        self.assertNotIn(dead, hub.clients)

    async def test_reauthorization_updates_identified_clients_only(self) -> None:
        hub = ClientHub()
        anonymous = _FakeClientWebSocketResponse()
        allowed = _FakeClientWebSocketResponse()
        denied = _FakeClientWebSocketResponse()
        hub.add(anonymous, is_editor=False)
        hub.add(allowed, user_id=1, is_editor=False)
        hub.add(denied, user_id=2, is_editor=True)
        authorize = AsyncMock(side_effect=lambda user_id: user_id == 1)

        await hub.reauthorize(authorize)

        self.assertFalse(hub.clients[anonymous].is_editor)
        self.assertTrue(hub.clients[allowed].is_editor)
        self.assertFalse(hub.clients[denied].is_editor)
        self.assertEqual(authorize.await_count, 2)

    async def test_close_all_isolates_failures_and_empties_registry(self) -> None:
        hub = ClientHub()
        healthy = _FakeClientWebSocketResponse()
        failing = _FakeClientWebSocketResponse()
        failing.close = AsyncMock(side_effect=RuntimeError("already gone"))
        hub.add(healthy)
        hub.add(failing)

        await hub.close_all()

        self.assertTrue(healthy.closed)
        self.assertEqual(hub.clients, {})


def make_server(*, handler=None, authorize=None, tickets=None):
    hub = ClientHub()
    application_handler = handler or AsyncMock()
    authorizer = authorize or AsyncMock(return_value=False)
    ticket_store = tickets or TicketStore(token_factory=lambda: "ticket")
    server = WebSocketServer(
        clients=hub,
        tickets=ticket_store,
        authorize=authorizer,
        handle_application_message=application_handler,
        health_snapshot=lambda: {"status": "ok", "clients": hub.client_count},
    )
    return server, hub, application_handler, authorizer, ticket_store


class TestWebSocketServer(unittest.IsolatedAsyncioTestCase):
    async def test_bind_failure_cleans_partial_runner(self) -> None:
        server, _, _, _, _ = make_server()
        app = MagicMock()
        runner = MagicMock()
        runner.setup = AsyncMock()
        runner.cleanup = AsyncMock()
        site = MagicMock()
        site.start = AsyncMock(side_effect=OSError("in use"))

        with (
            patch("floorplan.infrastructure.websocket.web.Application", return_value=app),
            patch("floorplan.infrastructure.websocket.web.AppRunner", return_value=runner),
            patch("floorplan.infrastructure.websocket.web.TCPSite", return_value=site),
        ):
            started = await server.start("127.0.0.1", 3210)

        self.assertFalse(started)
        runner.cleanup.assert_awaited_once_with()
        self.assertIsNone(server.runner)
        self.assertIsNone(server.site)

    async def test_invalid_known_and_unknown_messages_are_ignored(self) -> None:
        server, hub, handler, _, _ = make_server()
        socket = _FakeClientWebSocketResponse()
        hub.add(socket)

        await server.handle_payload(socket, {"type": "saveLayout", "layout": {"version": 99}})
        await server.handle_payload(socket, {"type": "futureMessage", "anything": True})

        handler.assert_not_awaited()

    async def test_authorize_message_stores_identity_even_when_denied(self) -> None:
        authorize = AsyncMock(return_value=False)
        server, hub, _, _, tickets = make_server(authorize=authorize)
        socket = _FakeClientWebSocketResponse()
        hub.add(socket)
        ticket = tickets.mint(777)

        await server.handle_payload(socket, {"type": "authorize", "ticket": ticket})

        state = hub.clients[socket]
        self.assertEqual(state.user_id, 777)
        self.assertFalse(state.is_editor)

    async def test_query_ticket_identity_is_available_to_application_handler(self) -> None:
        seen = []
        socket = _PreparedSocket([_FakeWSMessage(json.dumps({"type": "webviewReady"}))])
        authorize = AsyncMock(return_value=True)
        tickets = TicketStore(token_factory=lambda: "query-ticket")
        ticket = tickets.mint(888)
        server = None

        async def handler(_socket, _message):
            seen.append(server.clients.get(_socket))

        server, _, _, _, _ = make_server(
            handler=handler,
            authorize=authorize,
            tickets=tickets,
        )
        request = MagicMock()
        request.query = {"ticket": ticket}

        with patch("floorplan.infrastructure.websocket.web.WebSocketResponse", return_value=socket):
            await server.handle_ws(request)

        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0].user_id, 888)
        self.assertTrue(seen[0].is_editor)

    async def test_viewer_mutations_are_denied_before_delegation(self) -> None:
        server, hub, handler, _, _ = make_server()
        socket = _FakeClientWebSocketResponse()
        hub.add(socket, is_editor=False)
        valid_layout = {
            "version": 1,
            "cols": 1,
            "rows": 1,
            "tiles": [1],
            "furniture": [],
        }

        await server.handle_payload(socket, {"type": "saveLayout", "layout": valid_layout})

        handler.assert_not_awaited()


class _PreparedSocket(_FakeClientWebSocketResponse):
    async def prepare(self, request):
        return request


class TestCogUnload(unittest.IsolatedAsyncioTestCase):
    async def test_unload_closes_clients_and_cleans_server(self) -> None:
        bot = MagicMock()
        bot.guilds = []
        bot.is_owner = AsyncMock(return_value=False)
        cog = FloorplanCog(bot)
        socket = _FakeClientWebSocketResponse()
        cog._client_hub.add(socket)
        runner = MagicMock()
        runner.cleanup = AsyncMock()
        cog._websocket_server.runner = runner

        await cog.cog_unload()

        self.assertTrue(socket.closed)
        self.assertEqual(cog._clients, {})
        runner.cleanup.assert_awaited_once_with()
