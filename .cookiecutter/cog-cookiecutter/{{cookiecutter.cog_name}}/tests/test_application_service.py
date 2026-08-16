"""CounterService is fully testable without Red: a plain in-memory fake
satisfies the CounterRepository protocol, no unittest.mock needed."""

from __future__ import annotations

import unittest

from ..application import CounterService


class FakeCounterRepository:
    def __init__(self) -> None:
        self._counts: dict[int, int] = {}

    async def read(self, guild_id: int) -> int:
        return self._counts.get(guild_id, 0)

    async def increment(self, guild_id: int) -> int:
        count = self._counts.get(guild_id, 0) + 1
        self._counts[guild_id] = count
        return count


class TestCounterService(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.service = CounterService(FakeCounterRepository())

    async def test_show_defaults_to_zero(self) -> None:
        snapshot = await self.service.show(guild_id=1)

        self.assertEqual(snapshot.guild_id, 1)
        self.assertEqual(snapshot.count, 0)

    async def test_bump_increments_and_persists(self) -> None:
        first = await self.service.bump(guild_id=1)
        second = await self.service.bump(guild_id=1)

        self.assertEqual(first.count, 1)
        self.assertEqual(second.count, 2)

    async def test_bump_is_scoped_per_guild(self) -> None:
        await self.service.bump(guild_id=1)
        other_guild = await self.service.show(guild_id=2)

        self.assertEqual(other_guild.count, 0)
