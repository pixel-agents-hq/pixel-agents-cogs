"""Per-guild office "universe" registry.

Issue #4 split floorplan's single, all-guilds-merged office into one
independent, viewable "universe" per Discord server: its own agent
population and its own layout/seats. `pixelagents.application.office`'s
`OfficeService` (and `PresenceService`) classes are reused completely
unchanged -- only the *cardinality* of how many instances exist changes,
from one shared instance to one per guild, each fed only that guild's own
snapshots and broadcasting only to that guild's own connected clients.

A genuine agent (e.g. architect -- see docs/office-agent-identity-design.md)
has no guild scope at all, so it isn't tracked by any `GuildOffice` here.
`cog_base.py` keeps one separate, always-existing `OfficeService` instance
for genuine agents (`GenuineAgentSeatRepository`, below), broadcasting
unscoped to every connected client regardless of which guild's universe
they're viewing.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, cast

from pixelagents.application.office import OfficeService, SeatRecords
from pixelagents.application.presence import PresenceService

SendToGuild = Callable[[int, Mapping[str, object]], Awaitable[None]]


class GuildSeatRepository:
    """Narrows a floorplan `SettingsRepository` to one guild's own seats,
    matching `pixelagents.application.office.SeatRepository`'s protocol."""

    def __init__(self, repository: Any, guild_id: int) -> None:
        self._repository = repository
        self._guild_id = guild_id

    async def seats(self) -> SeatRecords:
        return cast(SeatRecords, await self._repository.guild_seats(self._guild_id))

    async def mutate_seats(self, mutation: Callable[[SeatRecords], Any]) -> Any:
        return await self._repository.mutate_guild_seats(self._guild_id, mutation)


class GenuineAgentSeatRepository:
    """Narrows a floorplan `SettingsRepository` to the one global seat
    store genuine agents share (they have no guild to key off), matching
    `pixelagents.application.office.SeatRepository`'s protocol."""

    def __init__(self, repository: Any) -> None:
        self._repository = repository

    async def seats(self) -> SeatRecords:
        return cast(SeatRecords, await self._repository.genuine_agent_seats())

    async def mutate_seats(self, mutation: Callable[[SeatRecords], Any]) -> Any:
        return await self._repository.mutate_genuine_agent_seats(mutation)


@dataclass(slots=True)
class GuildOffice:
    """One guild's independent office runtime -- its own agents, its own
    presence cache, broadcasting only to clients viewing this guild."""

    guild_id: int
    presence: PresenceService
    office: OfficeService = field(repr=False)


class UniverseRegistry:
    """Own one `GuildOffice` per guild, created lazily on first use."""

    def __init__(
        self,
        *,
        repository: Any,
        send_to_guild: SendToGuild,
        logger: logging.Logger | None = None,
    ) -> None:
        self._repository = repository
        self._send_to_guild = send_to_guild
        self._logger = logger or logging.getLogger(__name__)
        self._universes: dict[int, GuildOffice] = {}

    def get_or_create(self, guild_id: int) -> GuildOffice:
        universe = self._universes.get(guild_id)
        if universe is not None:
            return universe

        async def send(message: Mapping[str, object]) -> None:
            await self._send_to_guild(guild_id, message)

        presence = PresenceService(send)
        office = OfficeService(
            GuildSeatRepository(self._repository, guild_id),
            send,
            presence=presence,
            logger=self._logger,
        )
        universe = GuildOffice(guild_id=guild_id, presence=presence, office=office)
        self._universes[guild_id] = universe
        return universe

    def all(self) -> tuple[GuildOffice, ...]:
        return tuple(self._universes.values())


__all__ = [
    "GenuineAgentSeatRepository",
    "GuildOffice",
    "GuildSeatRepository",
    "UniverseRegistry",
]
