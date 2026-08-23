"""build_time_tool is fully testable without Red or corridor: it's a plain
RegisteredTool wrapping TimeService, exercised the same way
test_application_service.py exercises TimeService itself."""

from __future__ import annotations

import unittest
from zoneinfo import ZoneInfo

from corridor.domain import EMPLOYEE_KEY

from ..adapters.tools import TOOL_NAME, build_time_tool
from ..application import TimeService
from .test_application_service import FIXED_INSTANT, FakeClock

EPOCH = int(FIXED_INSTANT.timestamp())


class TestBuildTimeTool(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.tool = build_time_tool(TimeService(FakeClock()))

    def test_metadata(self) -> None:
        self.assertEqual(self.tool.name, TOOL_NAME)
        self.assertEqual(self.tool.required_group, EMPLOYEE_KEY)
        parameters = dict(self.tool.parameters)
        self.assertEqual(parameters["type"], "object")
        self.assertIn("timezone", parameters["properties"])  # type: ignore[operator]
        self.assertEqual(parameters["required"], [])

    async def test_handler_without_a_timezone_returns_utc_and_discord_markup(self) -> None:
        result = await self.tool.handler({})

        self.assertEqual(result["utc_iso"], FIXED_INSTANT.isoformat())
        self.assertEqual(result["epoch_seconds"], EPOCH)
        self.assertEqual(result["discord_markup"], f"<t:{EPOCH}:F>")
        self.assertNotIn("timezone", result)

    async def test_handler_with_a_valid_timezone_adds_a_localized_field(self) -> None:
        result = await self.tool.handler({"timezone": "America/New_York"})

        self.assertEqual(result["timezone"], "America/New_York")
        self.assertEqual(
            result["localized_iso"],
            FIXED_INSTANT.astimezone(ZoneInfo("America/New_York")).isoformat(),
        )

    async def test_handler_with_an_unknown_timezone_returns_an_error_instead(self) -> None:
        result = await self.tool.handler({"timezone": "Not/A_Real_Zone"})

        self.assertIn("error", result)
        self.assertIn("Not/A_Real_Zone", result["error"])
        self.assertNotIn("localized_iso", result)
