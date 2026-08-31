from __future__ import annotations

import unittest
from typing import Any

from ..infrastructure import CctvServer, ClientHub, TicketStore


class _Pipeline:
    def __init__(self, page: str, *, open_editor: bool) -> None:
        self.page = page
        self.open_editor = open_editor
        self.clients = ClientHub(page)
        self.messages: list[object] = []

    async def authorize(self, user_id: int) -> bool:
        return user_id == 42

    async def handle_message(self, socket: Any, message: object) -> None:
        self.messages.append(message)


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


if __name__ == "__main__":
    unittest.main()
