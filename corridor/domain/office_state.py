"""Framework-neutral office-state values shared across cogs."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, TypeAlias

RawLayout: TypeAlias = dict[str, Any]
SeatRecords: TypeAlias = dict[str, dict[str, Any]]
# Each office character's look, keyed by who wears it -- shared by both
# pages, so not part of either aggregate.
CharacterRecords: TypeAlias = dict[str, dict[str, Any]]


class OfficeStateKind(StrEnum):
    DISCORD = "discord"
    EDITOR = "editor"


@dataclass(frozen=True, slots=True)
class OfficeRef:
    """Which office aggregate: each guild has its own Discord office, and
    there is one editor office, which belongs to no guild."""

    kind: OfficeStateKind
    guild_id: int | None = None

    def __post_init__(self) -> None:
        if self.kind == OfficeStateKind.DISCORD:
            if type(self.guild_id) is not int or self.guild_id <= 0:
                raise ValueError("a discord office needs a guild id")
        elif self.guild_id is not None:
            raise ValueError("the editor office belongs to no guild")

    @classmethod
    def discord(cls, guild_id: int) -> OfficeRef:
        return cls(OfficeStateKind.DISCORD, guild_id)

    @classmethod
    def editor(cls) -> OfficeRef:
        return cls(OfficeStateKind.EDITOR)

    def describe(self) -> str:
        return self.kind.value if self.guild_id is None else f"{self.kind.value}/{self.guild_id}"


@dataclass(frozen=True, slots=True)
class OfficeState:
    kind: OfficeStateKind
    layout: RawLayout
    seats: SeatRecords
    revision: int
    # The guild a discord office belongs to; None for the editor office.
    guild_id: int | None = None

    @property
    def ref(self) -> OfficeRef:
        return OfficeRef(self.kind, self.guild_id)


@dataclass(frozen=True, slots=True)
class OfficeStateChanged:
    state: OfficeState


def copy_office_state(state: OfficeState) -> OfficeState:
    """Return a defensive copy of an otherwise frozen aggregate."""

    return OfficeState(
        kind=state.kind,
        layout=deepcopy(state.layout),
        seats=deepcopy(state.seats),
        revision=state.revision,
        guild_id=state.guild_id,
    )


__all__ = [
    "CharacterRecords",
    "OfficeRef",
    "OfficeState",
    "OfficeStateChanged",
    "OfficeStateKind",
    "RawLayout",
    "SeatRecords",
    "copy_office_state",
]
