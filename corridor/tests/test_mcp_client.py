"""McpClientPool against a real, locally-bound FastMCP server -- not
mocked, matching the "verified for real" bar test_a2a_server.py already
sets for the loopback socket this design's other half talks to. This test
builds its own minimal FastMCP server rather than importing suggestionbox's
real one: corridor's own test suite must never depend on a downstream
cog's package (the same reason test_a2a_server.py defines its own
DummyExecutor rather than importing architect's real one)."""

from __future__ import annotations

import asyncio
import unittest

import uvicorn
from mcp.server.fastmcp import FastMCP

from ..infrastructure.mcp_client import McpClientPool, McpRequestError

PORT = 8970


def _build_server() -> FastMCP:
    mcp = FastMCP("test-server", host="127.0.0.1", port=PORT, stateless_http=True)

    @mcp.tool()
    def echo(text: str) -> dict[str, str]:
        """Echoes text back."""
        return {"echo": text}

    @mcp.tool()
    def fail() -> str:
        """Always raises, so the server reports a tool error."""
        raise ValueError("deliberate failure")

    return mcp


class TestMcpClientPool(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.mcp = _build_server()
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

    async def test_list_tools_returns_the_real_servers_tools(self) -> None:
        tools = await self.pool.list_tools(self.base_url)

        self.assertEqual(sorted(tool.name for tool in tools), ["echo", "fail"])

    async def test_call_tool_returns_structured_content_as_a_mapping(self) -> None:
        result = await self.pool.call_tool(self.base_url, "echo", {"text": "hi"})

        self.assertEqual(result, {"echo": "hi"})

    async def test_call_tool_on_unknown_tool_raises(self) -> None:
        with self.assertRaises(McpRequestError):
            await self.pool.call_tool(self.base_url, "nope", {})

    async def test_call_tool_that_raises_server_side_raises_mcp_request_error(self) -> None:
        with self.assertRaises(McpRequestError):
            await self.pool.call_tool(self.base_url, "fail", {})

    async def test_list_tools_against_unreachable_server_raises(self) -> None:
        with self.assertRaises(McpRequestError):
            await self.pool.list_tools("http://127.0.0.1:1/mcp")


if __name__ == "__main__":
    unittest.main()
