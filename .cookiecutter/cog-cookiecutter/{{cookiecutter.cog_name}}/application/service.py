"""Framework-agnostic application logic.

CounterService depends only on the CounterRepository Protocol below, never on
Red's Config directly -- swap in any implementation (the real Red-backed one,
or a plain in-memory fake in tests) without touching this module.
"""

from __future__ import annotations

from typing import Protocol

from ..domain import CounterSnapshot


class CounterRepository(Protocol):
    """The persistence boundary CounterService depends on."""

    async def read(self, guild_id: int) -> int: ...

    async def increment(self, guild_id: int) -> int: ...


class CounterService:
    def __init__(self, repository: CounterRepository) -> None:
        self._repository = repository

    async def show(self, guild_id: int) -> CounterSnapshot:
        count = await self._repository.read(guild_id)
        return CounterSnapshot(guild_id=guild_id, count=count)

    async def bump(self, guild_id: int) -> CounterSnapshot:
        count = await self._repository.increment(guild_id)
        return CounterSnapshot(guild_id=guild_id, count=count)
