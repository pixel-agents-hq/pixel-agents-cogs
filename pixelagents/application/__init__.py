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
from .office_state import (
    InvalidDiscordLayoutError,
    OfficeLayoutNotSeededError,
    OfficeStateBackend,
    OfficeStateFacade,
    validate_discord_layout,
)
from .presence import PresenceService

__all__ = [
    "DEFAULT_PALETTE_COUNT",
    "JS_MAX_SAFE",
    "READING_TOOLS",
    "SUBAGENT_TOOL_NAMES",
    "InvalidDiscordLayoutError",
    "OfficeLayoutNotSeededError",
    "OfficeService",
    "OfficeStateBackend",
    "OfficeStateFacade",
    "PresenceService",
    "SeatRecords",
    "SeatRepository",
    "merge_seat_patch",
    "to_agent_id",
    "validate_discord_layout",
]
