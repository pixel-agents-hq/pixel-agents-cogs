from __future__ import annotations

import asyncio
import unittest
from copy import deepcopy
from typing import Any

from ..application import OfficeCharacterService


class _Storage:
    def __init__(self) -> None:
        self.records: dict[str, dict[str, Any]] = {}

    async def characters(self) -> dict[str, dict[str, Any]]:
        # Yield between read and write, so an unserialized mutation would race.
        await asyncio.sleep(0)
        return deepcopy(self.records)

    async def save_characters(self, characters: dict[str, dict[str, Any]]) -> None:
        await asyncio.sleep(0)
        self.records = deepcopy(characters)


class TestOfficeCharacterService(unittest.IsolatedAsyncioTestCase):
    async def test_concurrent_mutations_all_land(self) -> None:
        storage = _Storage()
        service = OfficeCharacterService(storage)

        def add(key: str) -> Any:
            def mutation(records: dict[str, dict[str, Any]]) -> str:
                records[key] = {"seed": f"px.{key}"}
                return key

            return mutation

        results = await asyncio.gather(*(service.mutate(add(f"user:{i}")) for i in range(5)))

        self.assertEqual([result for _, result in results], [f"user:{i}" for i in range(5)])
        self.assertEqual(sorted(storage.records), [f"user:{i}" for i in range(5)])

    async def test_reads_are_copies(self) -> None:
        storage = _Storage()
        service = OfficeCharacterService(storage)
        await service.mutate(lambda records: records.update({"user:1": {"seed": "px.a"}}))

        snapshot = await service.characters()
        snapshot["user:1"]["seed"] = "changed"

        self.assertEqual(storage.records["user:1"]["seed"], "px.a")


if __name__ == "__main__":
    unittest.main()
