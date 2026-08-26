"""ClientHub: bare connection tracking and message delivery, no editor/
identity state (see the module's own docstring for why -- there is
nothing to authorize yet). Fakes here are plain duck-typed doubles, not a
stubbed `aiohttp` module -- this package's own suite exercises the real
aiohttp package directly (see test_a2a_server.py's real listener tests),
unlike floorplan's fully-stubbed style."""

from __future__ import annotations

import json
import unittest

from ..infrastructure.client_hub import ClientHub


class FakeSocket:
    def __init__(self) -> None:
        self.closed = False
        self.sent: list[str] = []

    async def send_str(self, data: str) -> None:
        self.sent.append(data)

    async def close(self) -> None:
        self.closed = True


class TestClientHub(unittest.IsolatedAsyncioTestCase):
    async def test_broadcast_serializes_and_prunes_closed_clients(self) -> None:
        hub = ClientHub()
        live = FakeSocket()
        dead = FakeSocket()
        dead.closed = True
        hub.add(live)
        hub.add(dead)

        sent = await hub.broadcast({"type": "example", "nested": {"value": 1}})

        self.assertEqual(sent, 1)
        self.assertEqual(json.loads(live.sent[0])["nested"], {"value": 1})
        self.assertNotIn(dead, hub.clients)

    async def test_send_to_a_closed_socket_removes_it_and_reports_failure(self) -> None:
        hub = ClientHub()
        dead = FakeSocket()
        dead.closed = True
        hub.add(dead)

        sent = await hub.send_to(dead, {"type": "example"})

        self.assertFalse(sent)
        self.assertNotIn(dead, hub.clients)

    async def test_close_all_isolates_failures_and_empties_registry(self) -> None:
        hub = ClientHub()
        healthy = FakeSocket()

        class FailingSocket(FakeSocket):
            async def close(self) -> None:
                raise RuntimeError("already gone")

        failing = FailingSocket()
        hub.add(healthy)
        hub.add(failing)

        await hub.close_all()

        self.assertTrue(healthy.closed)
        self.assertEqual(hub.clients, set())

    async def test_broadcast_to_no_clients_is_a_no_op(self) -> None:
        hub = ClientHub()

        sent = await hub.broadcast({"type": "example"})

        self.assertEqual(sent, 0)
