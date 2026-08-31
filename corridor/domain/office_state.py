"""Framework-neutral office-state values shared across cogs."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, TypeAlias

RawLayout: TypeAlias = dict[str, Any]
SeatRecords: TypeAlias = dict[str, dict[str, Any]]


class OfficeStateKind(StrEnum):
    DISCORD = "discord"
    EDITOR = "editor"


@dataclass(frozen=True, slots=True)
class OfficeState:
    kind: OfficeStateKind
    layout: RawLayout
    seats: SeatRecords
    revision: int


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
    )


__all__ = [
    "OfficeState",
    "OfficeStateChanged",
    "OfficeStateKind",
    "RawLayout",
    "SeatRecords",
    "copy_office_state",
]
