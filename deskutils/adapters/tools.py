"""Registers `[p]deskutils time`'s TimeService as a cross-cog LLM tool in
corridor's tool registry, so pico (if loaded and enabled for a guild) can
call it directly instead of a user needing to run the Discord command by
hand -- see docs/corridor-tool-registry-design.md. Framework-neutral by
design: a plain JSON-Schema dict for `parameters`, a dict-in/dict-out
handler -- deskutils has no reason to take on a pydantic dependency just
for this optional integration.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from corridor.domain import EMPLOYEE_KEY, RegisteredTool

from ..application import TimeService, UnknownTimeZoneError

TOOL_NAME = "deskutils_time"


def build_time_tool(service: TimeService) -> RegisteredTool:
    async def handler(raw_input: Mapping[str, Any]) -> Mapping[str, Any]:
        snapshot = service.now()
        result: dict[str, Any] = {
            "utc_iso": snapshot.utc.isoformat(),
            "epoch_seconds": snapshot.epoch_seconds,
            "discord_markup": f"<t:{snapshot.epoch_seconds}:F>",
        }
        timezone = raw_input.get("timezone")
        if not timezone:
            return result
        try:
            zone = service.resolve_zone(str(timezone))
        except UnknownTimeZoneError:
            return {
                "error": (
                    f"Unknown time zone: {timezone!r}. Use an IANA name, e.g. 'America/New_York'."
                )
            }
        result["timezone"] = str(timezone)
        result["localized_iso"] = snapshot.utc.astimezone(zone).isoformat()
        return result

    return RegisteredTool(
        name=TOOL_NAME,
        description=(
            "Get the current date and time. Optionally pass an IANA timezone name "
            "(e.g. 'America/New_York') to also get it localized to that zone."
        ),
        parameters={
            "type": "object",
            "properties": {
                "timezone": {
                    "type": "string",
                    "description": "Optional IANA time zone name, e.g. 'America/New_York'.",
                }
            },
            "required": [],
        },
        handler=handler,
        required_group=EMPLOYEE_KEY,
    )


__all__ = ["TOOL_NAME", "build_time_tool"]
