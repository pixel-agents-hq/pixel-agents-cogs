"""In-process registry of MCP tools servers a registered A2A agent's own
tool-calling loop may call through, mediated entirely by corridor -- an
agent (architect today, more later) never talks to the registering cog
directly. See docs/suggestionbox-design.md.

Same register/unregister_owner/unregister shape `ToolRegistryService`/
`AgentDirectoryService` already follow, generalized a third time: this one
holds a live MCP client connection's cached tool list per registered
server, gated per `agent_key` rather than per Discord permission group.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any, Protocol

from mcp import types as mcp_types

from ..domain.agent_tool_server import RegisteredMcpServer
from ..domain.models import RegisteredTool
from ..infrastructure.mcp_client import McpRequestError

log = logging.getLogger("red.corridor")


class McpTools(Protocol):
    """The slice of `McpClientPool` this registry depends on -- same
    "Protocol naming the slice of a concrete client a service depends on"
    shape `architect`'s own `ToolLoopService.ToolLLM` already uses, so a
    test can stand in a plain fake without needing a real MCP server."""

    async def list_tools(self, base_url: str) -> tuple[mcp_types.Tool, ...]: ...

    async def call_tool(
        self, base_url: str, name: str, arguments: Mapping[str, Any]
    ) -> Mapping[str, Any]: ...


class AgentToolServerRegistry:
    """One registry per bot process, not per guild -- same scoping as
    ToolRegistryService/AgentDirectoryService."""

    def __init__(self, client_pool: McpTools) -> None:
        self._client_pool = client_pool
        # base_url -> (owner, server, that server's tools, cached at
        # registration time -- see docs/suggestionbox-design.md §9 on why
        # this isn't re-fetched on a schedule).
        self._servers: dict[str, tuple[str, RegisteredMcpServer, tuple[RegisteredTool, ...]]] = {}

    async def register(self, server: RegisteredMcpServer, *, owner: str) -> str | None:
        """Connects to `server.base_url`, fetches its current tool list,
        and stores it under `owner` -- returns an error string on failure
        (never raises, same never-raise convention `A2AServer.start`
        already uses), `None` on success. Re-registering the same
        `base_url` under the same `owner` re-fetches and overwrites --
        idempotent across repeat `cog_load` calls. A name collision from a
        *different* owner is a real authoring conflict, so it raises
        instead of silently letting one shadow the other -- same collision
        policy as `ToolRegistryService.register`/`AgentDirectoryService.
        register`."""

        existing = self._servers.get(server.base_url)
        if existing is not None and existing[0] != owner:
            raise ValueError(
                f"MCP server {server.base_url!r} is already registered by {existing[0]!r}, "
                f"cannot re-register it for {owner!r}"
            )
        try:
            tools = await self._client_pool.list_tools(server.base_url)
        except McpRequestError as exc:
            log.warning("corridor: could not register MCP server %r: %s", server.base_url, exc)
            return str(exc)
        registered = tuple(self._wrap_tool(tool, server.base_url) for tool in tools)
        self._servers[server.base_url] = (owner, server, registered)
        return None

    def unregister_owner(self, owner: str) -> None:
        """The registering cog's own responsibility, called from its own
        cog_unload -- same convention as ToolRegistryService.
        unregister_owner."""

        for url in [u for u, (o, _, _) in self._servers.items() if o == owner]:
            del self._servers[url]

    def unregister(self, base_url: str) -> None:
        """Remove one server by its URL, regardless of owner. A no-op if
        `base_url` isn't registered."""

        self._servers.pop(base_url, None)

    async def list_tools_for(self, agent_key: str) -> tuple[RegisteredTool, ...]:
        """Every tool from every registered server whose own `agent_allowed
        (agent_key)` returns True -- an agent's own tool loop calls this
        fresh every turn (see docs/suggestionbox-design.md §6), so a bot
        owner flipping suggestionbox's Components V2 toggle takes effect
        on that agent's very next turn, no cog reload required."""

        allowed: list[RegisteredTool] = []
        for _owner, server, tools in self._servers.values():
            try:
                if not await server.agent_allowed(agent_key):
                    continue
            except Exception:
                log.warning(
                    "corridor: agent_allowed check failed for MCP server %r; omitting its tools",
                    server.base_url,
                    exc_info=True,
                )
                continue
            allowed.extend(tools)
        return tuple(allowed)

    def _wrap_tool(self, tool: mcp_types.Tool, base_url: str) -> RegisteredTool:
        name = tool.name
        description = tool.description or name

        async def handler(_ctx: object, arguments: Mapping[str, object]) -> Mapping[str, object]:
            try:
                return await self._client_pool.call_tool(base_url, name, arguments)
            except McpRequestError as exc:
                return {"status": "error", "error": str(exc)}

        return RegisteredTool(
            name=name,
            description=description,
            parameters=tool.inputSchema,
            handler=handler,
        )


__all__ = ["AgentToolServerRegistry"]
