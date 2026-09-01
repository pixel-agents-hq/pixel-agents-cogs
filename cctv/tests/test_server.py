from __future__ import annotations

import asyncio
import unittest
from typing import Any

from aiohttp import ClientSession

from ..infrastructure import CctvServer, ClientHub, TicketStore


class _Pipeline:
    def __init__(self, page: str, *, open_editor: bool) -> None:
        self.page = page
        self.open_editor = open_editor
        self.clients = ClientHub(page)
        self.messages: list[object] = []
        self.allowed = True
        self.message_received = asyncio.Event()

    async def authorize(self, user_id: int) -> bool:
        return self.allowed and user_id == 42

    async def handle_message(self, socket: Any, message: object) -> None:
        self.messages.append(message)
        self.message_received.set()


class _Socket:
    closed = False

    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send_str(self, payload: str) -> None:
        self.sent.append(payload)

    async def close(self) -> None:
        self.closed = True


class TestCctvServer(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.discord = _Pipeline("discord", open_editor=False)
        self.editor = _Pipeline("editor", open_editor=True)
        self.tickets = TicketStore(token_factory=lambda: "ticket")
        self.server = CctvServer(
            self.discord,
            self.editor,
            self.tickets,
            lambda: {"status": "ok"},
        )

    async def test_one_listener_registers_both_routes(self) -> None:
        self.assertTrue(await self.server.start("127.0.0.1", 0))
        self.addAsyncCleanup(self.server.stop)

        assert self.server.app is not None
        routes = {route.resource.canonical for route in self.server.app.router.routes()}
        self.assertEqual(
            routes,
            {"/cctv/discord/ws", "/cctv/editor/ws", "/cctv/health"},
        )

    async def test_discord_write_requires_authorized_ticket(self) -> None:
        socket = _Socket()
        self.discord.clients.add(socket)  # type: ignore[arg-type]
        layout = {
            "version": 1,
            "cols": 1,
            "rows": 1,
            "tiles": [1],
            "furniture": [],
        }

        await self.server.handle_payload(
            "discord",
            socket,
            {"type": "saveLayout", "layout": layout},  # type: ignore[arg-type]
        )
        self.assertEqual(self.discord.messages, [])

        self.tickets.mint(42)
        await self.server.handle_payload(
            "discord",
            socket,
            {"type": "authorize", "ticket": "ticket"},  # type: ignore[arg-type]
        )
        await self.server.handle_payload(
            "discord",
            socket,
            {"type": "saveLayout", "layout": layout},  # type: ignore[arg-type]
        )
        self.assertEqual(len(self.discord.messages), 1)

    async def test_editor_write_is_open(self) -> None:
        socket = _Socket()
        self.editor.clients.add(socket, is_editor=True)  # type: ignore[arg-type]

        await self.server.handle_payload(
            "editor",
            socket,  # type: ignore[arg-type]
            {"type": "saveAgentSeats", "seats": {"agent": {"palette": 1}}},
        )

        self.assertEqual(len(self.editor.messages), 1)

    async def test_discord_permission_is_rechecked_for_each_write(self) -> None:
        socket = _Socket()
        self.discord.clients.add(socket)  # type: ignore[arg-type]
        self.tickets.mint(42)
        await self.server.handle_payload(
            "discord",
            socket,
            {"type": "authorize", "ticket": "ticket"},  # type: ignore[arg-type]
        )
        self.discord.allowed = False

        await self.server.handle_payload(
            "discord",
            socket,
            {
                "type": "saveLayout",
                "layout": {
                    "version": 1,
                    "cols": 1,
                    "rows": 1,
                    "tiles": [1],
                    "furniture": [],
                },
            },  # type: ignore[arg-type]
        )

        self.assertEqual(self.discord.messages, [])
        state = self.discord.clients.get(socket)  # type: ignore[arg-type]
        assert state is not None
        self.assertFalse(state.is_editor)

    async def test_live_routes_share_listener_and_enforce_distinct_write_policy(self) -> None:
        self.assertTrue(await self.server.start("127.0.0.1", 0))
        self.addAsyncCleanup(self.server.stop)
        assert self.server.site is not None
        raw_server = self.server.site._server  # type: ignore[attr-defined]
        assert raw_server is not None
        port = raw_server.sockets[0].getsockname()[1]
        layout = {
            "version": 1,
            "cols": 1,
            "rows": 1,
            "tiles": [1],
            "furniture": [],
        }

        async with ClientSession() as session:
            unauthorized = await session.ws_connect(f"http://127.0.0.1:{port}/cctv/discord/ws")
            await _wait_for(lambda: self.discord.clients.client_count == 1)
            await unauthorized.send_json({"type": "saveLayout", "layout": layout})
            await asyncio.sleep(0.02)
            self.assertEqual(self.discord.messages, [])
            await unauthorized.close()
            await _wait_for(lambda: self.discord.clients.client_count == 0)

            self.tickets.mint(42)
            authorized = await session.ws_connect(
                f"http://127.0.0.1:{port}/cctv/discord/ws?ticket=ticket"
            )
            await authorized.send_json({"type": "saveLayout", "layout": layout})
            await asyncio.wait_for(self.discord.message_received.wait(), timeout=1)
            self.assertEqual(len(self.discord.messages), 1)
            await authorized.close()

            editor = await session.ws_connect(f"http://127.0.0.1:{port}/cctv/editor/ws")
            await editor.send_json({"type": "saveAgentSeats", "seats": {"agent": {"palette": 1}}})
            await asyncio.wait_for(self.editor.message_received.wait(), timeout=1)
            self.assertEqual(len(self.editor.messages), 1)
            await editor.close()


async def _wait_for(predicate: Any) -> None:
    for _ in range(100):
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condition was not reached")


if __name__ == "__main__":
    unittest.main()
