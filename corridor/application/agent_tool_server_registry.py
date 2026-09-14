"""In-process registry of MCP tools servers a registered A2A agent's own
tool-calling loop may call through, mediated entirely by corridor -- an
agent (architect today, more later) never talks to the registering cog
directly. See docs/suggestionbox-design.md.

Same register/unregister_owner/unregister shape `ToolRegistryService`/
`AgentDirectoryService` already follow, generalized a third time: this one
holds a live MCP client connection's cached tool list per registered
server, gated per `agent_key` rather than per Discord permission group.

A registered server's tool list (and its initialization instructions) is a
short-TTL cache with stale fallback, the same policy `ModelCatalogService`
(`corridor/application/model_catalog_service.py`) already uses for
LiteLLM's model catalogue: a fresh cache hit skips the network entirely, an
expired entry triggers one re-fetch on the next `list_tools_for()` call,
and a failed re-fetch just keeps serving the last-known tool list rather
than erroring or evicting it -- a third-party server that's briefly
unreachable shouldn't break an agent's tool loop mid-conversation. See
docs/suggestionbox-design.md's "Why the MCP client is stateless" rationale
for why this is a TTL, not a persistent connection kept open for a push
notification.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from mcp import types as mcp_types

from ..domain.agent_tool_server import McpCallOptions, RegisteredMcpServer
from ..domain.models import RegisteredTool
from ..infrastructure.mcp_client import McpRequestError, McpToolListing

log = logging.getLogger("red.corridor")

# Short enough that a third-party server's schema change is picked up
# without a manual refresh or cog reload; long enough that an agent
# holding a fast back-and-forth conversation isn't paying a network
# round-trip per registered server on every turn. Same constant name/value
# shape as `model_catalog_service.FRESH_TTL_SECONDS`.
FRESH_TTL_SECONDS = 5 * 60.0


class McpTools(Protocol):
    """The slice of `McpClientPool` this registry depends on -- same
    "Protocol naming the slice of a concrete client a service depends on"
    shape `architect`'s own `ToolLoopService.ToolLLM` already uses, so a
    test can stand in a plain fake without needing a real MCP server."""

    async def discover_tools(self, base_url: str) -> McpToolListing: ...

    async def call_tool(
        self,
        base_url: str,
        name: str,
        arguments: Mapping[str, Any],
        *,
        timeout_seconds: float | None = None,
    ) -> Mapping[str, Any]: ...


@dataclass(frozen=True, slots=True)
class _ServerEntry:
    owner: str
    server: RegisteredMcpServer
    tools: tuple[RegisteredTool, ...]
    fetched_at: float


class AgentToolServerRegistry:
    """One registry per bot process, not per guild -- same scoping as
    ToolRegistryService/AgentDirectoryService."""

    def __init__(
        self,
        client_pool: McpTools,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._client_pool = client_pool
        self._clock = clock
        self._servers: dict[str, _ServerEntry] = {}

    async def register(self, server: RegisteredMcpServer, *, owner: str) -> str | None:
        """Connects to `server.base_url`, fetches its current tool list,
        and stores it under `owner` -- returns an error string on failure
        (never raises, same never-raise convention `A2AServer.start`
        already uses), `None` on success. Re-registering the same
        `base_url` under the same `owner` re-fetches and overwrites --
        idempotent across repeat `cog_load` calls, and resets the TTL
        clock too, so it always forces an immediate refresh regardless of
        how fresh the previous cache entry still was. A name collision
        from a *different* owner is a real authoring conflict, so it
        raises instead of silently letting one shadow the other -- same
        collision policy as `ToolRegistryService.register`/
        `AgentDirectoryService.register`."""

        existing = self._servers.get(server.base_url)
        if existing is not None and existing.owner != owner:
            raise ValueError(
                f"MCP server {server.base_url!r} is already registered by {existing.owner!r}, "
                f"cannot re-register it for {owner!r}"
            )
        try:
            listing = await self._client_pool.discover_tools(server.base_url)
        except McpRequestError as exc:
            log.warning("corridor: could not register MCP server %r: %s", server.base_url, exc)
            return str(exc)
        if self._servers.get(server.base_url) is not existing:
            return "MCP registration changed during discovery; retry registration"
        registered = tuple(
            self._wrap_tool(tool, server.base_url, listing.instructions) for tool in listing.tools
        )
        self._servers[server.base_url] = _ServerEntry(
            owner=owner, server=server, tools=registered, fetched_at=self._clock()
        )
        return None

    def unregister_owner(self, owner: str) -> None:
        """The registering cog's own responsibility, called from its own
        cog_unload -- same convention as ToolRegistryService.
        unregister_owner."""

        for url in [u for u, entry in self._servers.items() if entry.owner == owner]:
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
        on that agent's very next turn, no cog reload required.

        Also refreshes each server's cached tool *list* (not just the
        gate) once its entry is older than `FRESH_TTL_SECONDS` -- see the
        module docstring for the TTL-with-stale-fallback policy."""

        allowed: list[RegisteredTool] = []
        for base_url in list(self._servers):
            entry = self._servers.get(base_url)
            if entry is None:
                continue
            entry = await self._refresh_if_stale(base_url, entry)
            if self._servers.get(base_url) is None:
                continue  # unregistered while the refresh above was in flight
            try:
                if not await entry.server.agent_allowed(agent_key):
                    continue
            except Exception:
                log.warning(
                    "corridor: agent_allowed check failed for MCP server %r; omitting its tools",
                    entry.server.base_url,
                    exc_info=True,
                )
                continue
            allowed.extend(entry.tools)
        return tuple(allowed)

    async def _refresh_if_stale(self, base_url: str, entry: _ServerEntry) -> _ServerEntry:
        """Re-fetches `base_url`'s tool list and instructions once `entry`
        is older than `FRESH_TTL_SECONDS`. A failed re-fetch logs and
        returns `entry` unchanged -- a briefly-unreachable server keeps
        serving its last-known tools rather than losing them for the
        turn, same stale-on-failure policy `ModelCatalogService.
        list_models` uses."""

        if self._clock() - entry.fetched_at <= FRESH_TTL_SECONDS:
            return entry
        try:
            listing = await self._client_pool.discover_tools(base_url)
        except McpRequestError as exc:
            log.warning(
                "corridor: could not refresh MCP server %r tool list; serving stale cache: %s",
                base_url,
                exc,
            )
            return entry
        refreshed = _ServerEntry(
            owner=entry.owner,
            server=entry.server,
            tools=tuple(
                self._wrap_tool(tool, base_url, listing.instructions) for tool in listing.tools
            ),
            fetched_at=self._clock(),
        )
        # Only write back if nothing else (an explicit re-`register()`, an
        # `unregister()`) changed this entry while the fetch above was
        # in flight -- otherwise we'd resurrect a since-unregistered
        # server or clobber a newer explicit registration.
        if self._servers.get(base_url) is entry:
            self._servers[base_url] = refreshed
        return refreshed

    def _wrap_tool(self, tool: mcp_types.Tool, base_url: str, instructions: str) -> RegisteredTool:
        name = tool.name
        description = tool.description or name

        async def handler(ctx: object, arguments: Mapping[str, object]) -> Mapping[str, object]:
            # `ctx` is opaque to every other RegisteredTool consumer (a
            # Discord commands.Context, or None) -- an agent-side adapter
            # that needs to bound one specific call's own network timeout
            # (e.g. a registered server's blocking wait_for_job) passes an
            # McpCallOptions here instead. Anything else just falls back to
            # McpClientPool's own default timeout, same as before this
            # existed.
            timeout_seconds = ctx.timeout_seconds if isinstance(ctx, McpCallOptions) else None
            try:
                return await self._client_pool.call_tool(
                    base_url, name, arguments, timeout_seconds=timeout_seconds
                )
            except McpRequestError as exc:
                return {"status": "error", "error": str(exc)}

        return RegisteredTool(
            name=name,
            description=description,
            parameters=tool.inputSchema,
            handler=handler,
            server_instructions=instructions,
        )


__all__ = ["FRESH_TTL_SECONDS", "AgentToolServerRegistry"]
