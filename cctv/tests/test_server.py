from __future__ import annotations

import asyncio
import json
import unittest
from typing import Any

from aiohttp import ClientSession, WSMsgType

from ..infrastructure import OFFICE_UNAVAILABLE_CLOSE_CODE, CctvServer, ClientHub, TicketStore
from ..infrastructure.server import MAX_CLIENT_MESSAGE_BYTES

GUILD = 10
OTHER_GUILD = 20
MEMBER = 42
STRANGER = 7
LAYOUT = {"version": 1, "cols": 1, "rows": 1, "tiles": [1], "furniture": []}


class _Pipeline:
    def __init__(self, page: str, *, guild_id: int | None, private: bool = False) -> None:
        self.page = page
        self.private = private
        self._guild_id = guild_id
        self.clients = ClientHub(page)
        self.messages: list[object] = []
        self.allowed = True
        self.members = {MEMBER}
        self.message_received = asyncio.Event()

    @property
    def guild_id(self) -> int | None:
        return self._guild_id

    async def authorize(self, user_id: int) -> bool:
        return self.allowed and user_id == MEMBER

    async def can_view(self, user_id: int) -> bool:
        return user_id in self.members

    async def handle_message(self, socket: Any, message: object) -> None:
        self.messages.append(message)
        self.message_received.set()
        if getattr(message, "type", None) == "webviewReady":
            await self.clients.send_to(socket, {"type": "layoutLoaded"})


class _Socket:
    closed = False

    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send_str(self, payload: str) -> None:
        self.sent.append(payload)

    async def close(self, *, code: int = 1000) -> None:
        self.closed = True
        self.close_code = code


class TestCctvServer(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.office = _Pipeline(f"office/{GUILD}", guild_id=GUILD)
        self.other = _Pipeline(f"office/{OTHER_GUILD}", guild_id=OTHER_GUILD)
        self.offices = {GUILD: self.office, OTHER_GUILD: self.other}
        self.editor = _Pipeline("editor", guild_id=None)
        tokens = iter(f"ticket-{n}" for n in range(100))
        self.tickets = TicketStore(token_factory=lambda: next(tokens))
        self.server = CctvServer(
            self.offices.get,
            lambda: tuple(self.offices.values()),
            self.editor,
            self.tickets,
            lambda: {"status": "ok"},
        )

    async def test_one_listener_registers_every_route(self) -> None:
        self.assertTrue(await self.server.start("127.0.0.1", 0))
        self.addAsyncCleanup(self.server.stop)

        assert self.server.app is not None
        routes = {route.resource.canonical for route in self.server.app.router.routes()}
        self.assertEqual(
            routes,
            {"/cctv/office/{guild_id}/ws", "/cctv/editor/ws", "/cctv/health"},
        )

    async def test_an_office_write_requires_a_ticket_minted_for_that_guild(self) -> None:
        socket = _Socket()
        self.office.clients.add(socket)  # type: ignore[arg-type]
        write = {"type": "saveLayout", "layout": LAYOUT}

        await self.server.handle_payload(self.office, socket, write)  # type: ignore[arg-type]
        self.assertEqual(self.office.messages, [])

        wrong_guild = self.tickets.mint(MEMBER, OTHER_GUILD)
        await self.server.handle_payload(
            self.office,  # type: ignore[arg-type]
            socket,  # type: ignore[arg-type]
            {"type": "authorize", "ticket": wrong_guild},
        )
        await self.server.handle_payload(self.office, socket, write)  # type: ignore[arg-type]
        self.assertEqual(self.office.messages, [])

        ticket = self.tickets.mint(MEMBER, GUILD)
        await self.server.handle_payload(
            self.office,  # type: ignore[arg-type]
            socket,  # type: ignore[arg-type]
            {"type": "authorize", "ticket": ticket},
        )
        await self.server.handle_payload(self.office, socket, write)  # type: ignore[arg-type]
        self.assertEqual(len(self.office.messages), 1)

    async def test_a_ticket_is_single_use(self) -> None:
        ticket = self.tickets.mint(MEMBER, GUILD)

        self.assertEqual(self.tickets.consume(ticket, GUILD), MEMBER)
        self.assertIsNone(self.tickets.consume(ticket, GUILD))

    async def test_an_anonymous_editor_socket_watches_but_cannot_write(self) -> None:
        socket = _Socket()
        self.editor.clients.add(socket)  # type: ignore[arg-type]

        for write in (
            {"type": "saveLayout", "layout": LAYOUT},
            {"type": "saveAgentSeats", "seats": {"agent": {"palette": 1}}},
            {"type": "importLayout", "layout": LAYOUT},
        ):
            await self.server.handle_payload(self.editor, socket, write)  # type: ignore[arg-type]
        self.assertEqual(self.editor.messages, [])

        await self.server.handle_payload(
            self.editor,  # type: ignore[arg-type]
            socket,  # type: ignore[arg-type]
            {"type": "webviewReady"},
        )
        self.assertEqual([m.type for m in self.editor.messages], ["webviewReady"])  # type: ignore[attr-defined]
        self.assertEqual(socket.sent, [json.dumps({"type": "layoutLoaded"})])

    async def test_an_editor_write_needs_an_editor_ticket_for_an_allowed_user(self) -> None:
        socket = _Socket()
        self.editor.clients.add(socket)  # type: ignore[arg-type]
        write = {"type": "saveLayout", "layout": LAYOUT}

        for refused in (
            self.tickets.mint(MEMBER, GUILD),  # a guild office's ticket
            self.tickets.mint(STRANGER, None),  # signed in, but not let in by the gate
            "forged",
        ):
            with self.subTest(ticket=refused):
                await self.server.handle_payload(
                    self.editor,  # type: ignore[arg-type]
                    socket,  # type: ignore[arg-type]
                    {"type": "authorize", "ticket": refused},
                )
                await self.server.handle_payload(self.editor, socket, write)  # type: ignore[arg-type]
                self.assertEqual(self.editor.messages, [])

        await self.server.handle_payload(
            self.editor,  # type: ignore[arg-type]
            socket,  # type: ignore[arg-type]
            {"type": "authorize", "ticket": self.tickets.mint(MEMBER, None)},
        )
        await self.server.handle_payload(self.editor, socket, write)  # type: ignore[arg-type]
        self.assertEqual(len(self.editor.messages), 1)

    async def test_an_editor_ticket_does_not_open_a_guild_office(self) -> None:
        socket = _Socket()
        self.office.clients.add(socket)  # type: ignore[arg-type]

        await self.server.handle_payload(
            self.office,  # type: ignore[arg-type]
            socket,  # type: ignore[arg-type]
            {"type": "authorize", "ticket": self.tickets.mint(MEMBER, None)},
        )
        await self.server.handle_payload(
            self.office,  # type: ignore[arg-type]
            socket,  # type: ignore[arg-type]
            {"type": "saveLayout", "layout": LAYOUT},
        )

        self.assertEqual(self.office.messages, [])

    async def test_editor_permission_is_rechecked_for_each_write(self) -> None:
        socket = _Socket()
        self.editor.clients.add(socket)  # type: ignore[arg-type]
        await self.server.handle_payload(
            self.editor,  # type: ignore[arg-type]
            socket,  # type: ignore[arg-type]
            {"type": "authorize", "ticket": self.tickets.mint(MEMBER, None)},
        )
        self.editor.allowed = False

        await self.server.handle_payload(
            self.editor,  # type: ignore[arg-type]
            socket,  # type: ignore[arg-type]
            {"type": "saveLayout", "layout": LAYOUT},
        )

        self.assertEqual(self.editor.messages, [])
        state = self.editor.clients.get(socket)  # type: ignore[arg-type]
        assert state is not None
        self.assertFalse(state.is_editor)

    async def test_office_permission_is_rechecked_for_each_write(self) -> None:
        socket = _Socket()
        self.office.clients.add(socket)  # type: ignore[arg-type]
        await self.server.handle_payload(
            self.office,  # type: ignore[arg-type]
            socket,  # type: ignore[arg-type]
            {"type": "authorize", "ticket": self.tickets.mint(MEMBER, GUILD)},
        )
        self.office.allowed = False

        await self.server.handle_payload(
            self.office,  # type: ignore[arg-type]
            socket,  # type: ignore[arg-type]
            {"type": "saveLayout", "layout": LAYOUT},
        )

        self.assertEqual(self.office.messages, [])
        state = self.office.clients.get(socket)  # type: ignore[arg-type]
        assert state is not None
        self.assertFalse(state.is_editor)


class _LiveServer(unittest.IsolatedAsyncioTestCase):
    private = False

    async def asyncSetUp(self) -> None:
        self.office = _Pipeline(f"office/{GUILD}", guild_id=GUILD, private=self.private)
        self.other = _Pipeline(f"office/{OTHER_GUILD}", guild_id=OTHER_GUILD)
        self.offices = {GUILD: self.office, OTHER_GUILD: self.other}
        self.editor = _Pipeline("editor", guild_id=None)
        self.tickets = TicketStore()
        self.origin: str | None = None

        async def origin() -> str | None:
            return self.origin

        self.server = CctvServer(
            self.offices.get,
            lambda: tuple(self.offices.values()),
            self.editor,
            self.tickets,
            lambda: {"status": "ok"},
            public_origin=origin,
            authorize_timeout=0.3,
        )
        self.assertTrue(await self.server.start("127.0.0.1", 0))
        self.addAsyncCleanup(self.server.stop)
        assert self.server.site is not None
        raw_server = self.server.site._server  # type: ignore[attr-defined]
        self.base = f"http://127.0.0.1:{raw_server.sockets[0].getsockname()[1]}"


class TestOfficeRouting(_LiveServer):
    async def test_a_socket_joins_only_its_own_guilds_office(self) -> None:
        async with ClientSession() as session:
            socket = await session.ws_connect(f"{self.base}/cctv/office/{GUILD}/ws")
            await _wait_for(lambda: self.office.clients.client_count == 1)
            await socket.send_json({"type": "webviewReady"})
            await asyncio.wait_for(self.office.message_received.wait(), timeout=1)
            self.assertEqual(self.other.clients.client_count, 0)
            self.assertEqual(self.other.messages, [])
            await socket.close()

    async def test_a_public_office_write_needs_an_in_band_ticket(self) -> None:
        async with ClientSession() as session:
            socket = await session.ws_connect(
                # A ticket in the URL is ignored: only the in-band one counts.
                f"{self.base}/cctv/office/{GUILD}/ws?ticket={self.tickets.mint(MEMBER, GUILD)}"
            )
            await socket.send_json({"type": "saveLayout", "layout": LAYOUT})
            await asyncio.sleep(0.05)
            self.assertEqual(self.office.messages, [])

            await socket.send_json(
                {"type": "authorize", "ticket": self.tickets.mint(MEMBER, GUILD)}
            )
            await socket.send_json({"type": "saveLayout", "layout": LAYOUT})
            await asyncio.wait_for(self.office.message_received.wait(), timeout=1)
            self.assertEqual(len(self.office.messages), 1)
            await socket.close()

    async def test_every_unavailable_office_closes_the_same_way(self) -> None:
        async with ClientSession() as session:
            for path in ("999", "abc", "0" * 25, "-1"):
                with self.subTest(path=path):
                    socket = await session.ws_connect(f"{self.base}/cctv/office/{path}/ws")
                    await socket.send_json({"type": "webviewReady"})
                    message = await socket.receive()
                    self.assertEqual(message.type, WSMsgType.CLOSE)
                    self.assertEqual(socket.close_code, OFFICE_UNAVAILABLE_CLOSE_CODE)
        self.assertEqual(self.office.clients.client_count, 0)

    async def test_stop_closes_every_offices_clients(self) -> None:
        async with ClientSession() as session:
            first = await session.ws_connect(f"{self.base}/cctv/office/{GUILD}/ws")
            second = await session.ws_connect(f"{self.base}/cctv/office/{OTHER_GUILD}/ws")
            await _wait_for(
                lambda: (
                    self.office.clients.client_count == 1 and self.other.clients.client_count == 1
                )
            )
            await self.server.stop()
            await first.receive()
            await second.receive()
            self.assertTrue(first.closed and second.closed)

    async def test_a_live_editor_socket_is_read_only_until_an_editor_signs_in(self) -> None:
        async with ClientSession() as session:
            editor = await session.ws_connect(f"{self.base}/cctv/editor/ws")
            await editor.send_json({"type": "saveAgentSeats", "seats": {"agent": {"palette": 1}}})
            await editor.send_json({"type": "saveLayout", "layout": LAYOUT})
            await editor.send_json({"type": "webviewReady"})
            # Anonymous viewers still see the office...
            message = await asyncio.wait_for(editor.receive(), timeout=1)
            self.assertEqual(json.loads(message.data), {"type": "layoutLoaded"})
            # ...but none of their writes reached it.
            self.assertEqual([m.type for m in self.editor.messages], ["webviewReady"])  # type: ignore[attr-defined]

            await editor.send_json({"type": "authorize", "ticket": self.tickets.mint(MEMBER, None)})
            await editor.send_json({"type": "saveLayout", "layout": LAYOUT})
            await _wait_for(lambda: len(self.editor.messages) == 2)
            self.assertEqual(self.editor.messages[-1].type, "saveLayout")  # type: ignore[attr-defined]
            await editor.close()

    async def test_an_editor_ticket_does_not_open_an_unavailable_office(self) -> None:
        async with ClientSession() as session:
            socket = await session.ws_connect(f"{self.base}/cctv/office/abc/ws")
            await socket.send_json({"type": "authorize", "ticket": self.tickets.mint(MEMBER, None)})
            message = await asyncio.wait_for(socket.receive(), timeout=1)
            self.assertEqual(message.type, WSMsgType.CLOSE)
            self.assertEqual(socket.close_code, OFFICE_UNAVAILABLE_CLOSE_CODE)

    async def test_an_oversized_frame_closes_the_socket_unread(self) -> None:
        async with ClientSession() as session:
            editor = await session.ws_connect(f"{self.base}/cctv/editor/ws")
            try:
                await editor.send_str("x" * (MAX_CLIENT_MESSAGE_BYTES + 1))
                message = await asyncio.wait_for(editor.receive(), timeout=2)
                self.assertIn(message.type, {WSMsgType.CLOSE, WSMsgType.CLOSED, WSMsgType.ERROR})
            except ConnectionError:
                pass  # the server dropped the connection mid-send
            await _wait_for(lambda: self.editor.clients.client_count == 0)
            self.assertEqual(self.editor.messages, [])


class TestPrivateOffice(_LiveServer):
    private = True

    async def _receive_text(self, socket: Any) -> dict[str, Any]:
        message = await asyncio.wait_for(socket.receive(), timeout=1)
        self.assertEqual(message.type, WSMsgType.TEXT, message)
        return json.loads(message.data)  # type: ignore[no-any-return]

    async def test_a_member_sees_the_office_after_presenting_a_ticket(self) -> None:
        async with ClientSession() as session:
            socket = await session.ws_connect(f"{self.base}/cctv/office/{GUILD}/ws")
            await socket.send_json({"type": "webviewReady"})
            await _wait_for(lambda: self.office.clients.client_count == 1)
            # Nothing reaches an unproven socket, broadcasts included.
            self.assertEqual(await self.office.clients.broadcast({"type": "secret"}), 0)

            await socket.send_json(
                {"type": "authorize", "ticket": self.tickets.mint(MEMBER, GUILD)}
            )

            # The held webviewReady is replayed once the socket is proven.
            self.assertEqual(await self._receive_text(socket), {"type": "layoutLoaded"})
            self.assertEqual(await self.office.clients.broadcast({"type": "now"}), 1)
            await socket.close()

    async def test_a_socket_without_a_ticket_is_closed_having_seen_nothing(self) -> None:
        async with ClientSession() as session:
            socket = await session.ws_connect(f"{self.base}/cctv/office/{GUILD}/ws")
            await socket.send_json({"type": "webviewReady"})
            await socket.send_json({"type": "saveLayout", "layout": LAYOUT})

            message = await asyncio.wait_for(socket.receive(), timeout=2)

            self.assertEqual(message.type, WSMsgType.CLOSE)
            self.assertEqual(socket.close_code, OFFICE_UNAVAILABLE_CLOSE_CODE)
            self.assertEqual(self.office.messages, [])

    async def test_a_non_members_or_another_guilds_ticket_is_refused(self) -> None:
        async with ClientSession() as session:
            for ticket in (
                self.tickets.mint(STRANGER, GUILD),
                self.tickets.mint(MEMBER, OTHER_GUILD),
                "forged",
            ):
                with self.subTest(ticket=ticket):
                    socket = await session.ws_connect(f"{self.base}/cctv/office/{GUILD}/ws")
                    await socket.send_json({"type": "authorize", "ticket": ticket})
                    message = await asyncio.wait_for(socket.receive(), timeout=1)
                    self.assertEqual(message.type, WSMsgType.CLOSE)
                    self.assertEqual(socket.close_code, OFFICE_UNAVAILABLE_CLOSE_CODE)
        self.assertEqual(self.office.messages, [])

    async def test_a_socket_from_another_origin_is_refused(self) -> None:
        self.origin = "https://dash.example.com"
        async with ClientSession() as session:
            socket = await session.ws_connect(
                f"{self.base}/cctv/office/{GUILD}/ws",
                headers={"Origin": "https://evil.example"},
            )
            await socket.send_json(
                {"type": "authorize", "ticket": self.tickets.mint(MEMBER, GUILD)}
            )
            message = await asyncio.wait_for(socket.receive(), timeout=1)
            self.assertEqual(message.type, WSMsgType.CLOSE)

            allowed = await session.ws_connect(
                f"{self.base}/cctv/office/{GUILD}/ws",
                headers={"Origin": "https://dash.example.com"},
            )
            await allowed.send_json(
                {"type": "authorize", "ticket": self.tickets.mint(MEMBER, GUILD)}
            )
            await allowed.send_json({"type": "webviewReady"})
            self.assertEqual(await self._receive_text(allowed), {"type": "layoutLoaded"})
            await allowed.close()


async def _wait_for(predicate: Any) -> None:
    for _ in range(100):
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condition was not reached")


if __name__ == "__main__":
    unittest.main()
