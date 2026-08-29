"""build_mcp_server against a real, locally-bound FastMCP server, driven by
corridor's own real McpClientPool over the real Streamable HTTP wire --
not mocked, matching the "verified for real" bar corridor's own
test_mcp_client.py sets for the other half of this same protocol. Proves
the MCP tool schemas FastMCP infers from report_error/suggest_improvement's
signatures round-trip correctly and reach FeedbackService with the right
arguments -- the Discord-posting side of FeedbackService itself is a
plain fake `post` callable here (Discord posting is corridor's own concern,
covered by corridor's test_channel_reply.py; this cog's own adapter-layer
wiring to corridor is covered by test_cog_commands.py instead)."""

from __future__ import annotations

import asyncio
import unittest
from collections.abc import Sequence

import uvicorn

from corridor.infrastructure.mcp_client import McpClientPool

from ..application.feedback_service import FeedbackService
from ..infrastructure.mcp_server import build_mcp_server

PORT = 8935


class FakeRepository:
    async def feedback_channel(self) -> tuple[int, int] | None:
        return (10, 20)


class FakePoster:
    def __init__(self) -> None:
        self.calls: list[tuple[int, int, str, str, Sequence[tuple[str, str]]]] = []

    async def __call__(
        self,
        guild_id: int,
        channel_id: int,
        title: str,
        description: str,
        fields: Sequence[tuple[str, str]],
    ) -> bool:
        self.calls.append((guild_id, channel_id, title, description, fields))
        return True


class TestMcpServer(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.poster = FakePoster()
        service = FeedbackService(FakeRepository(), post=self.poster)
        self.mcp = build_mcp_server(service, host="127.0.0.1", port=PORT)
        config = uvicorn.Config(
            self.mcp.streamable_http_app(), host="127.0.0.1", port=PORT, log_level="warning"
        )
        self.server = uvicorn.Server(config)
        self.task = asyncio.create_task(self.server.serve())
        while not self.server.started and not self.task.done():  # noqa: ASYNC110
            await asyncio.sleep(0)
        self.pool = McpClientPool()
        self.base_url = f"http://127.0.0.1:{PORT}/mcp"

    async def asyncTearDown(self) -> None:
        self.server.should_exit = True
        await self.task

    async def test_lists_both_tools(self) -> None:
        tools = await self.pool.list_tools(self.base_url)

        self.assertEqual(
            sorted(tool.name for tool in tools), ["report_error", "suggest_improvement"]
        )

    async def test_report_error_reaches_feedback_service_and_posts(self) -> None:
        result = await self.pool.call_tool(
            self.base_url,
            "report_error",
            {
                "source": "architect",
                "what_happened": "misread a tool description",
                "expected": "a string",
                "actual": "an int",
                "severity": "high",
            },
        )

        self.assertEqual(result, {"status": "ok"})
        [(guild_id, channel_id, title, description, fields)] = self.poster.calls
        self.assertEqual((guild_id, channel_id), (10, 20))
        self.assertIn("high", title)
        self.assertEqual(description, "misread a tool description")
        self.assertIn(["Source", "architect"], [list(f) for f in fields])

    async def test_report_error_defaults_severity_to_medium(self) -> None:
        await self.pool.call_tool(
            self.base_url,
            "report_error",
            {
                "source": "architect",
                "what_happened": "x",
                "expected": "y",
                "actual": "z",
            },
        )

        [(_, _, title, _, _)] = self.poster.calls
        self.assertIn("medium", title)

    async def test_report_error_rejects_an_invalid_severity(self) -> None:
        result = await self.pool.call_tool(
            self.base_url,
            "report_error",
            {
                "source": "architect",
                "what_happened": "x",
                "expected": "y",
                "actual": "z",
                "severity": "critical",
            },
        )

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["error"], "invalid_severity")
        self.assertEqual(self.poster.calls, [])

    async def test_suggest_improvement_reaches_feedback_service_and_posts(self) -> None:
        result = await self.pool.call_tool(
            self.base_url,
            "suggest_improvement",
            {
                "source": "architect",
                "area": "tool descriptions",
                "observation": "unclear",
                "suggestion": "clarify",
            },
        )

        self.assertEqual(result, {"status": "ok"})
        [(guild_id, channel_id, title, description, fields)] = self.poster.calls
        self.assertEqual((guild_id, channel_id), (10, 20))
        self.assertEqual(description, "unclear")
        self.assertIn(["Suggestion", "clarify"], [list(f) for f in fields])


if __name__ == "__main__":
    unittest.main()
