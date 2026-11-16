"""Erasing one Discord account's office data, for Red's data deletion API.

Corridor persists the office characters and every office's seats as opaque
JSON; this is the layer that knows which of those keys belong to a Discord
account, so it is the one place an account's records are deleted:

- its character (`user:<id>` in the shared character records), and the
  avatar PNG rendered from that character's seed code;
- its seat record (keyed by the webview agent id `to_agent_id(user_id)`)
  in every stored office: each guild's Discord office, including guilds the
  bot has left, and the editor office.

Seats are read and rewritten straight through corridor, not the validating
facade: an office whose stored layout no longer validates must still have
the account's seat removed.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, TypeVar

from ..domain import AgentKey
from ..domain.characters import character_key
from .characters import CharacterRecords, avatar_filename
from .office import to_agent_id

if TYPE_CHECKING:
    from corridor.domain import OfficeRef, OfficeState, SeatRecords

MutationResult = TypeVar("MutationResult")


class CorridorUserDataGateway(Protocol):
    async def office_characters(self) -> CharacterRecords: ...

    async def mutate_office_characters(
        self, mutation: Callable[[CharacterRecords], MutationResult]
    ) -> tuple[CharacterRecords, MutationResult]: ...

    async def office_state_refs(self) -> tuple[OfficeRef, ...]: ...

    async def office_state(self, ref: OfficeRef) -> OfficeState | None: ...

    async def mutate_office_seats(
        self, ref: OfficeRef, mutation: Callable[[SeatRecords], MutationResult]
    ) -> tuple[OfficeState, MutationResult]: ...


class UserDataService:
    def __init__(self, corridor: CorridorUserDataGateway, avatar_directory: Path) -> None:
        self._corridor = corridor
        self._avatars = avatar_directory

    async def forget_user(self, user_id: int) -> None:
        """Delete everything stored about `user_id`. Idempotent: an account
        with nothing stored changes nothing (no office revision is bumped)."""

        await self._forget_character(user_id)
        seat = str(to_agent_id(user_id))
        for ref in await self._corridor.office_state_refs():
            state = await self._corridor.office_state(ref)
            if state is None or seat not in state.seats:
                continue
            await self._corridor.mutate_office_seats(ref, lambda seats: seats.pop(seat, None))

    async def _forget_character(self, user_id: int) -> None:
        key = character_key(AgentKey(0, user_id))
        if key not in await self._corridor.office_characters():
            return

        def remove(records: CharacterRecords) -> tuple[Any, set[str]]:
            removed = records.pop(key, None)
            worn = {seed for raw in records.values() if (seed := _raw_seed(raw)) is not None}
            return removed, worn

        _, (removed, still_worn) = await self._corridor.mutate_office_characters(remove)
        # The raw `seed` field, not a parsed record: an older record this
        # version no longer parses still had its avatar rendered from it.
        seed = _raw_seed(removed)
        # Another account or agent wearing the same seed code keeps its avatar.
        if seed is None or seed in still_worn:
            return
        await asyncio.to_thread((self._avatars / avatar_filename(seed)).unlink, True)


def _raw_seed(raw: object) -> str | None:
    """A stored record's `seed` string, whether or not the record parses.
    Safe as a path: `avatar_filename` hashes it."""

    seed = raw.get("seed") if isinstance(raw, Mapping) else None
    return seed if isinstance(seed, str) and seed else None


__all__ = ["CorridorUserDataGateway", "UserDataService"]
