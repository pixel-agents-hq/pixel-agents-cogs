"""Exercises RedOfficeStateRepository against the fake Config installed by
the package-root conftest.py."""

from __future__ import annotations

import unittest

from ..infrastructure import RedOfficeStateRepository


class TestGetOrCreate(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.repository = RedOfficeStateRepository.create(cog=object())

    async def test_unseeded_kind_returns_a_blank_aggregate(self) -> None:
        state = await self.repository.get_or_create("discord")

        self.assertEqual(state.kind, "discord")
        self.assertEqual(state.layout, {})
        self.assertEqual(state.seats, {})
        self.assertEqual(state.revision, 0)

    async def test_get_or_create_persists_the_blank_aggregate(self) -> None:
        await self.repository.get_or_create("discord")

        state = await self.repository.get_or_create("discord")

        self.assertEqual(state.revision, 0)

    async def test_the_two_kinds_are_independent(self) -> None:
        await self.repository.set_layout("discord", {"a": 1})

        editor_state = await self.repository.get_or_create("editor")

        self.assertEqual(editor_state.layout, {})


class TestSetLayout(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.repository = RedOfficeStateRepository.create(cog=object())

    async def test_set_layout_persists_and_increments_revision(self) -> None:
        updated = await self.repository.set_layout("discord", {"cols": 3})

        self.assertEqual(updated.layout, {"cols": 3})
        self.assertEqual(updated.revision, 1)

        reread = await self.repository.get_or_create("discord")
        self.assertEqual(reread.layout, {"cols": 3})
        self.assertEqual(reread.revision, 1)

    async def test_set_layout_preserves_existing_seats(self) -> None:
        await self.repository.mutate_seats(
            "discord", lambda seats: seats.setdefault("-1", {"palette": 2})
        )

        updated = await self.repository.set_layout("discord", {"cols": 3})

        self.assertEqual(updated.seats, {"-1": {"palette": 2}})

    async def test_repeated_set_layout_keeps_incrementing_revision(self) -> None:
        await self.repository.set_layout("discord", {"cols": 1})
        second = await self.repository.set_layout("discord", {"cols": 2})

        self.assertEqual(second.revision, 2)


class TestMutateSeats(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.repository = RedOfficeStateRepository.create(cog=object())

    async def test_mutation_return_value_is_passed_through(self) -> None:
        def mutation(seats: dict) -> str:
            seats["-1"] = {"palette": 1}
            return "ok"

        updated, result = await self.repository.mutate_seats("editor", mutation)

        self.assertEqual(result, "ok")
        self.assertEqual(updated.seats, {"-1": {"palette": 1}})
        self.assertEqual(updated.revision, 1)

    async def test_mutate_seats_preserves_existing_layout(self) -> None:
        await self.repository.set_layout("editor", {"cols": 5})

        updated, _ = await self.repository.mutate_seats(
            "editor", lambda seats: seats.setdefault("-1", {})
        )

        self.assertEqual(updated.layout, {"cols": 5})

    async def test_the_two_kinds_have_independent_seats(self) -> None:
        await self.repository.mutate_seats(
            "discord", lambda seats: seats.setdefault("-1", {"palette": 0})
        )

        editor_state = await self.repository.get_or_create("editor")

        self.assertEqual(editor_state.seats, {})


if __name__ == "__main__":
    unittest.main()
