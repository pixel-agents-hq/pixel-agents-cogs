"""Atomic persistence for the office characters both pages draw."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from copy import deepcopy
from typing import Protocol, TypeVar

from ..domain import CharacterRecords

MutationResult = TypeVar("MutationResult")


class OfficeCharacterStorage(Protocol):
    async def characters(self) -> CharacterRecords: ...
    async def save_characters(self, characters: CharacterRecords) -> None: ...


class OfficeCharacterService:
    """One lock serializes every read-modify-write of the character records,
    so two members setting their characters at once can't drop either."""

    def __init__(self, storage: OfficeCharacterStorage) -> None:
        self._storage = storage
        self._lock = asyncio.Lock()

    async def characters(self) -> CharacterRecords:
        async with self._lock:
            return deepcopy(await self._storage.characters())

    async def mutate(
        self, mutation: Callable[[CharacterRecords], MutationResult]
    ) -> tuple[CharacterRecords, MutationResult]:
        async with self._lock:
            characters = deepcopy(await self._storage.characters())
            result = mutation(characters)
            await self._storage.save_characters(characters)
            return deepcopy(characters), result


__all__ = ["OfficeCharacterService", "OfficeCharacterStorage"]
