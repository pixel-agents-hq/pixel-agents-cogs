"""Application services that drive the pixel-agents office visualization."""

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

__all__ = [
    "DEFAULT_PALETTE_COUNT",
    "JS_MAX_SAFE",
    "READING_TOOLS",
    "SUBAGENT_TOOL_NAMES",
    "OfficeService",
    "PresenceService",
    "SeatRecords",
    "SeatRepository",
    "merge_seat_patch",
    "to_agent_id",
]
