"""Corridor's MCP client -- talks to any registered `RegisteredMcpServer`
(suggestionbox's own server today, see docs/suggestionbox-design.md) over
MCP's Streamable HTTP transport, using the official `mcp` SDK.

Pinned `mcp>=1.29,<2` (see `corridor/info.json`): `mcp` 2.0 renamed
`FastMCP` to `MCPServer` and reshuffled several client-side modules, and
also forces a `pydantic>=2.12` floor that collides with the
`pydantic>=2.11.3,<2.12` ceiling every other cog in this repo is pinned to
(see that pin's own comment in `corridor/info.json`). `mcp<2` keeps the
API this module (and `suggestionbox`'s own server) is written against.

Opens a fresh `streamable_http_client`/`ClientSession` pair per call rather
than holding one open across calls -- unlike `LiteLLMClient`'s one
reusable `aiohttp.ClientSession` (reused because pico/architect's chat
completions are frequent, latency-sensitive traffic), a registered
server's tools are called rarely (an agent reporting one error), so the
extra connection-setup cost is a good trade for never needing reconnect-
on-drop or session-id bookkeeping across arbitrarily long idle gaps.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from mcp import ClientSession
from mcp import types as mcp_types
from mcp.client.streamable_http import streamable_http_client

log = logging.getLogger("red.corridor")


class McpRequestError(RuntimeError):
    """Raised on any failure to reach a registered MCP server, or a tool
    call that server reports as an error -- callers (`AgentToolServerRegistry`)
    catch this and fail closed, the same convention `LLMRequestError`
    already sets for corridor's other outbound client, `LiteLLMClient`."""


class McpClientPool:
    """Stateless beyond logging -- one instance shared corridor-wide, since
    there is no per-connection state kept between calls (see module
    docstring on why each call opens its own connection)."""

    def __init__(self, *, logger: logging.Logger | None = None) -> None:
        self._log = logger or log

    async def list_tools(self, base_url: str) -> tuple[mcp_types.Tool, ...]:
        """Every tool `base_url` currently advertises. Raises
        `McpRequestError` on any connection/protocol failure -- callers
        decide what "a server that can't even be listed" means for their
        own registration flow."""

        try:
            async with streamable_http_client(base_url) as (read, write, _get_session_id):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    result = await session.list_tools()
                    return tuple(result.tools)
        except Exception as exc:
            raise McpRequestError(f"could not list tools from {base_url}: {exc}") from exc

    async def call_tool(
        self, base_url: str, name: str, arguments: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        """Calls `name` on the server at `base_url` and returns its result
        as a plain JSON-object-shaped mapping -- `RegisteredTool.handler`'s
        own contract. Raises `McpRequestError` on a connection/protocol
        failure or a result the server itself flagged as an error
        (`CallToolResult.isError`)."""

        try:
            async with streamable_http_client(base_url) as (read, write, _get_session_id):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    result = await session.call_tool(name, dict(arguments))
        except Exception as exc:
            raise McpRequestError(f"could not call tool {name!r} on {base_url}: {exc}") from exc

        if result.isError:
            raise McpRequestError(
                f"tool {name!r} on {base_url} returned an error: {_result_text(result)}"
            )
        return _result_to_mapping(result)


def _result_text(result: mcp_types.CallToolResult) -> str:
    texts = [block.text for block in result.content if isinstance(block, mcp_types.TextContent)]
    return "\n".join(texts) if texts else "(no text content)"


def _result_to_mapping(result: mcp_types.CallToolResult) -> dict[str, Any]:
    """Prefers `structuredContent` (already a JSON object) when the server
    provided one; otherwise falls back to joined text content under a
    single `"text"` key -- same "small, stable, JSON-serializable" bar
    `corridor-tool-registry-design.md` sets for `RegisteredTool` results."""

    if result.structuredContent is not None:
        return dict(result.structuredContent)
    texts = [block.text for block in result.content if isinstance(block, mcp_types.TextContent)]
    return {"text": "\n".join(texts)} if texts else {}


__all__ = ["McpClientPool", "McpRequestError"]
