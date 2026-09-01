"""Application services that drive the pixel-agents office visualization.

The office-state facade stays lazy so importing the plain-stdlib outbound
builders does not pull Red's Config dependency into contract tooling.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .office import (
    DEFAULT_PALETTE_COUNT,
    JS_MAX_SAFE,
    READING_TOOLS,
    SUBAGENT_TOOL_NAMES,
    OfficeService,
    SeatRecords,
    SeatRepository,
    merge_seat_patch,
    to_agent_id,
)
from .presence import PresenceService

if TYPE_CHECKING:
    from .office_state import (
        OfficeStateFacade,
        OfficeStateSeatRepository,
        OfficeStateUnavailableError,
        OfficeStateValidationError,
        validate_seat_records,
    )

__all__ = [
    "DEFAULT_PALETTE_COUNT",
    "JS_MAX_SAFE",
    "READING_TOOLS",
    "SUBAGENT_TOOL_NAMES",
    "OfficeService",
    "OfficeStateFacade",
    "OfficeStateSeatRepository",
    "OfficeStateUnavailableError",
    "OfficeStateValidationError",
    "PresenceService",
    "SeatRecords",
    "SeatRepository",
    "merge_seat_patch",
    "to_agent_id",
    "validate_seat_records",
]

_OFFICE_STATE_EXPORTS = {
    "OfficeStateFacade",
    "OfficeStateSeatRepository",
    "OfficeStateUnavailableError",
    "OfficeStateValidationError",
    "validate_seat_records",
}


def __getattr__(name: str) -> object:
    if name not in _OFFICE_STATE_EXPORTS:
        raise AttributeError(name)
    from . import office_state

    value = getattr(office_state, name)
    globals()[name] = value
    return value
