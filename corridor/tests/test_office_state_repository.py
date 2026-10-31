from __future__ import annotations

import unittest

from ..domain import OfficeRef, OfficeState, OfficeStateKind
from ..infrastructure import RedOfficeStateRepository

GUILD_A = 111
GUILD_B = 222


def _discord(guild_id: int, **overrides: object) -> OfficeState:
    values: dict[str, object] = {
        "kind": OfficeStateKind.DISCORD,
        "layout": {"version": 1},
        "seats": {"42": {"seatId": "desk-a"}},
        "revision": 2,
        "guild_id": guild_id,
    }
    values.update(overrides)
    return OfficeState(**values)  # type: ignore[arg-type]


class TestRedOfficeStateRepository(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.repository = RedOfficeStateRepository.create(cog=object())

    async def test_every_aggregate_starts_absent(self) -> None:
        self.assertIsNone(await self.repository.state(OfficeRef.discord(GUILD_A)))
        self.assertIsNone(await self.repository.state(OfficeRef.editor()))

    async def test_aggregates_are_stored_independently(self) -> None:
        guild_a = _discord(GUILD_A)
        guild_b = _discord(GUILD_B, layout={"version": 3}, seats={}, revision=9)
        editor = OfficeState(
            kind=OfficeStateKind.EDITOR,
            layout={"version": 2},
            seats={"agent": {"palette": 3}},
            revision=7,
        )

        await self.repository.save(guild_a)
        await self.repository.save(guild_b)
        await self.repository.save(editor)

        self.assertEqual(await self.repository.state(OfficeRef.discord(GUILD_A)), guild_a)
        self.assertEqual(await self.repository.state(OfficeRef.discord(GUILD_B)), guild_b)
        self.assertEqual(await self.repository.state(OfficeRef.editor()), editor)

    async def test_the_global_discord_office_is_no_longer_registered(self) -> None:
        self.assertNotIn("discord_state", self.repository._config._global_data)  # noqa: SLF001

    async def test_reads_and_writes_are_defensive_copies(self) -> None:
        layout = {"version": 1, "objects": [{"id": "desk-a"}]}
        await self.repository.save(_discord(GUILD_A, layout=layout, seats={}, revision=1))
        layout["objects"][0]["id"] = "changed"
        loaded = await self.repository.state(OfficeRef.discord(GUILD_A))
        assert loaded is not None
        loaded.layout["objects"][0]["id"] = "also-changed"

        reloaded = await self.repository.state(OfficeRef.discord(GUILD_A))
        assert reloaded is not None
        self.assertEqual(reloaded.layout["objects"][0]["id"], "desk-a")

    async def test_characters_start_empty_and_round_trip(self) -> None:
        self.assertEqual(await self.repository.characters(), {})
        record = {"seed": "px.a", "source": "derived", "profile": {"body": "flat"}}

        await self.repository.save_characters({"user:42": record})
        loaded = await self.repository.characters()
        loaded["user:42"]["seed"] = "changed"

        self.assertEqual(await self.repository.characters(), {"user:42": record})


class TestOfficeRef(unittest.TestCase):
    def test_a_discord_office_needs_a_guild_and_the_editor_has_none(self) -> None:
        for bad in (None, 0, -1, True):
            with self.subTest(guild_id=bad), self.assertRaises(ValueError):
                OfficeRef(OfficeStateKind.DISCORD, bad)  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            OfficeRef(OfficeStateKind.EDITOR, GUILD_A)

    def test_a_state_knows_its_ref(self) -> None:
        self.assertEqual(_discord(GUILD_A).ref, OfficeRef.discord(GUILD_A))


if __name__ == "__main__":
    unittest.main()
