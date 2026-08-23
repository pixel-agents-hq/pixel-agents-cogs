"""EventBusService is fully testable without Red: plain async closures
stand in for subscriber handlers, no unittest.mock needed."""

from __future__ import annotations

import unittest
from dataclasses import dataclass

from ..application import EventBusService


@dataclass(frozen=True, slots=True)
class _Ping:
    value: str


@dataclass(frozen=True, slots=True)
class _Pong:
    value: str


def _recorder(sink: list) -> object:
    """An async handler that appends every event it receives to `sink`.

    Deliberately not `sink.append` directly: `append` is synchronous, and
    `EventBusService.publish` always `await`s a handler's return value --
    passing a sync callable there raises `TypeError: object NoneType can't
    be used in 'await' expression`, which publish's own per-subscriber
    isolation then catches and logs, silently masking the mistake (a real
    bug caught while first writing this test file: assertions still passed
    because `append` already ran before the failed `await`, hiding that
    every single call was actually raising)."""

    async def handler(event: object) -> None:
        sink.append(event)

    return handler


class TestEventBusService(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.bus = EventBusService()

    async def test_publish_with_no_subscribers_is_a_noop(self) -> None:
        await self.bus.publish(_Ping("hi"))  # must not raise

    async def test_subscriber_receives_the_published_event(self) -> None:
        received: list[_Ping] = []
        self.bus.subscribe(_Ping, _recorder(received), owner="A")

        await self.bus.publish(_Ping("hi"))

        self.assertEqual(received, [_Ping("hi")])

    async def test_dispatch_is_scoped_to_the_exact_published_type(self) -> None:
        pings: list[_Ping] = []
        pongs: list[_Pong] = []
        self.bus.subscribe(_Ping, _recorder(pings), owner="A")
        self.bus.subscribe(_Pong, _recorder(pongs), owner="A")

        await self.bus.publish(_Ping("hi"))

        self.assertEqual(pings, [_Ping("hi")])
        self.assertEqual(pongs, [])

    async def test_multiple_subscribers_all_receive_the_event_in_order(self) -> None:
        order: list[str] = []

        async def first(event: _Ping) -> None:
            order.append(f"first:{event.value}")

        async def second(event: _Ping) -> None:
            order.append(f"second:{event.value}")

        self.bus.subscribe(_Ping, first, owner="A")
        self.bus.subscribe(_Ping, second, owner="B")

        await self.bus.publish(_Ping("hi"))

        self.assertEqual(order, ["first:hi", "second:hi"])

    async def test_one_raising_handler_does_not_block_the_others(self) -> None:
        async def raises(event: _Ping) -> None:
            raise RuntimeError("boom")

        received: list[_Ping] = []
        self.bus.subscribe(_Ping, raises, owner="broken")
        self.bus.subscribe(_Ping, _recorder(received), owner="healthy")

        await self.bus.publish(_Ping("hi"))  # must not raise

        self.assertEqual(received, [_Ping("hi")])

    async def test_unsubscribe_owner_drops_only_that_owners_handlers(self) -> None:
        a: list[_Ping] = []
        b: list[_Ping] = []
        self.bus.subscribe(_Ping, _recorder(a), owner="A")
        self.bus.subscribe(_Ping, _recorder(b), owner="B")

        self.bus.unsubscribe_owner("A")
        await self.bus.publish(_Ping("hi"))

        self.assertEqual(a, [])
        self.assertEqual(b, [_Ping("hi")])

    async def test_unsubscribe_owner_drops_across_every_event_type(self) -> None:
        pings: list[_Ping] = []
        pongs: list[_Pong] = []
        self.bus.subscribe(_Ping, _recorder(pings), owner="A")
        self.bus.subscribe(_Pong, _recorder(pongs), owner="A")

        self.bus.unsubscribe_owner("A")
        await self.bus.publish(_Ping("hi"))
        await self.bus.publish(_Pong("hi"))

        self.assertEqual(pings, [])
        self.assertEqual(pongs, [])

    async def test_unsubscribe_owner_for_unknown_owner_is_a_noop(self) -> None:
        self.bus.unsubscribe_owner("nobody")  # must not raise


if __name__ == "__main__":
    unittest.main()
