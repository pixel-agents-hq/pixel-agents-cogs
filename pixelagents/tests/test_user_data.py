"""Red's data deletion API: an account's character, avatar and seats."""

from __future__ import annotations

import json
import unittest
from collections.abc import Callable
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, TypeVar
from unittest.mock import AsyncMock, MagicMock

from corridor.domain import OfficeRef, OfficeState, SeatRecords

from .. import __red_end_user_data_statement__
from ..application.characters import avatar_filename
from ..application.office import to_agent_id
from ..application.user_data import UserDataService
from ..pixelagents import PixelAgents

MutationResult = TypeVar("MutationResult")
USER = 42
OTHER = 43
REQUESTERS = ("discord_deleted_user", "owner", "user", "user_strict")


def _record(seed: str) -> dict[str, Any]:
    return {"seed": seed, "source": "custom", "profile": {"body": "flat"}}


class _Corridor:
    def __init__(self) -> None:
        self.characters: dict[str, dict[str, Any]] = {}
        self.offices: dict[OfficeRef, OfficeState] = {}

    def add_office(self, ref: OfficeRef, seats: SeatRecords) -> None:
        self.offices[ref] = OfficeState(
            kind=ref.kind, layout={}, seats=seats, revision=1, guild_id=ref.guild_id
        )

    async def office_characters(self) -> dict[str, dict[str, Any]]:
        return deepcopy(self.characters)

    async def mutate_office_characters(
        self, mutation: Callable[[dict[str, dict[str, Any]]], MutationResult]
    ) -> tuple[dict[str, dict[str, Any]], MutationResult]:
        records = deepcopy(self.characters)
        result = mutation(records)
        self.characters = records
        return deepcopy(records), result

    async def office_state_refs(self) -> tuple[OfficeRef, ...]:
        return tuple(self.offices)

    async def office_state(self, ref: OfficeRef) -> OfficeState | None:
        return self.offices.get(ref)

    async def mutate_office_seats(
        self, ref: OfficeRef, mutation: Callable[[SeatRecords], MutationResult]
    ) -> tuple[OfficeState, MutationResult]:
        current = self.offices[ref]
        seats = deepcopy(current.seats)
        result = mutation(seats)
        self.offices[ref] = OfficeState(
            kind=current.kind,
            layout=current.layout,
            seats=seats,
            revision=current.revision + 1,
            guild_id=current.guild_id,
        )
        return self.offices[ref], result


class TestUserDataService(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.corridor = _Corridor()
        self._tmp = TemporaryDirectory()
        self.avatars = Path(self._tmp.name)
        self.service = UserDataService(self.corridor, self.avatars)
        self.seat = str(to_agent_id(USER))
        self.other_seat = str(to_agent_id(OTHER))

    def tearDown(self) -> None:
        self._tmp.cleanup()

    async def test_character_and_its_avatar_are_deleted(self) -> None:
        self.corridor.characters = {
            f"user:{USER}": _record("px.mine"),
            f"user:{OTHER}": _record("px.theirs"),
            "agent:architect": _record("px.agent"),
        }
        for seed in ("px.mine", "px.theirs"):
            (self.avatars / avatar_filename(seed)).write_bytes(b"png")

        await self.service.forget_user(USER)

        self.assertEqual(set(self.corridor.characters), {f"user:{OTHER}", "agent:architect"})
        self.assertFalse((self.avatars / avatar_filename("px.mine")).exists())
        self.assertTrue((self.avatars / avatar_filename("px.theirs")).exists())

    async def test_an_avatar_another_character_still_wears_is_kept(self) -> None:
        self.corridor.characters = {
            f"user:{USER}": _record("px.shared"),
            f"user:{OTHER}": _record("px.shared"),
        }
        (self.avatars / avatar_filename("px.shared")).write_bytes(b"png")

        await self.service.forget_user(USER)

        self.assertEqual(set(self.corridor.characters), {f"user:{OTHER}"})
        self.assertTrue((self.avatars / avatar_filename("px.shared")).exists())

    async def test_an_unparseable_records_avatar_is_deleted_by_its_raw_seed(self) -> None:
        # An older record this version no longer understands (no profile).
        self.corridor.characters = {f"user:{USER}": {"seed": "px.old", "source": "legacy"}}
        (self.avatars / avatar_filename("px.old")).write_bytes(b"png")

        await self.service.forget_user(USER)

        self.assertEqual(self.corridor.characters, {})
        self.assertFalse((self.avatars / avatar_filename("px.old")).exists())

    async def test_an_avatar_an_unparseable_record_still_wears_is_kept(self) -> None:
        self.corridor.characters = {
            f"user:{USER}": _record("px.shared"),
            f"user:{OTHER}": {"seed": "px.shared"},
        }
        (self.avatars / avatar_filename("px.shared")).write_bytes(b"png")

        await self.service.forget_user(USER)

        self.assertTrue((self.avatars / avatar_filename("px.shared")).exists())

    async def test_seats_are_deleted_in_every_stored_office(self) -> None:
        guild_a, guild_left, editor = (
            OfficeRef.discord(1),
            OfficeRef.discord(2),
            OfficeRef.editor(),
        )
        for ref in (guild_a, guild_left, editor):
            self.corridor.add_office(
                ref, {self.seat: {"seatId": "desk"}, self.other_seat: {"seatId": "desk-2"}}
            )

        await self.service.forget_user(USER)

        for ref in (guild_a, guild_left, editor):
            with self.subTest(ref=ref.describe()):
                self.assertEqual(
                    self.corridor.offices[ref].seats, {self.other_seat: {"seatId": "desk-2"}}
                )

    async def test_an_account_with_nothing_stored_changes_nothing(self) -> None:
        ref = OfficeRef.discord(1)
        self.corridor.add_office(ref, {self.other_seat: {"seatId": "desk"}})
        self.corridor.characters = {f"user:{OTHER}": _record("px.theirs")}
        self.corridor.mutate_office_characters = AsyncMock()  # type: ignore[method-assign]

        await self.service.forget_user(USER)

        self.assertEqual(self.corridor.offices[ref].revision, 1)
        self.corridor.mutate_office_characters.assert_not_awaited()

    async def test_a_missing_avatar_file_is_not_an_error(self) -> None:
        self.corridor.characters = {f"user:{USER}": _record("px.mine")}

        await self.service.forget_user(USER)

        self.assertEqual(self.corridor.characters, {})


class TestRedDeleteDataForUser(unittest.IsolatedAsyncioTestCase):
    async def test_every_requester_type_deletes_everything(self) -> None:
        for requester in REQUESTERS:
            with self.subTest(requester=requester):
                cog = MagicMock()
                cog.forget_user_data = AsyncMock()

                await PixelAgents.red_delete_data_for_user(cog, requester=requester, user_id=USER)

                cog.forget_user_data.assert_awaited_once_with(USER)

    async def test_deletion_before_load_fails_loudly_rather_than_silently(self) -> None:
        cog = PixelAgents(MagicMock())

        with self.assertRaises(RuntimeError):
            await cog.red_delete_data_for_user(requester="user", user_id=USER)


class TestEndUserDataStatement(unittest.TestCase):
    def test_statement_is_the_info_json_one(self) -> None:
        info = json.loads((Path(__file__).parents[1] / "info.json").read_text("utf-8"))
        self.assertEqual(__red_end_user_data_statement__, info["end_user_data_statement"])
