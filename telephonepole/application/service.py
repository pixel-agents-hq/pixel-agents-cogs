"""Framework-agnostic application logic for registering/unregistering
third-party MCP servers with corridor's `AgentToolServerRegistry`
(`corridor/application/agent_tool_server_registry.py`), and gating each
one's tools per registered A2A agent.

Depends only on the `ServerRepository` and `McpRegistrar` protocols below,
never on corridor/redbot/discord.py directly -- the same
"corridor-agnostic business logic, corridor-aware adapter" split
`suggestionbox/application/feedback_service.py` already uses for its own
corridor integration. The real `McpRegistrar` implementation
(`adapters/cog_base.py`'s `CorridorMcpRegistrar`) is the only place that
ever imports `corridor.domain.RegisteredMcpServer` or calls
`corridor.register_mcp_server`/`unregister_mcp_server`.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Protocol

from ..domain import ThirdPartyMcpServer

# agent_key -> may this agent use this server's tools. Same shape corridor's
# own `RegisteredMcpServer.agent_allowed` requires -- built here per-server
# via `TelephonepoleService._agent_allowed_for`, closing over `name` so one
# repository-backed toggle store can back many independently-gated servers.
AgentAllowedCheck = Callable[[str], Awaitable[bool]]


class ServerRepository(Protocol):
    """The persistence boundary `TelephonepoleService` depends on."""

    async def list_servers(self) -> tuple[ThirdPartyMcpServer, ...]: ...

    async def get_server(self, name: str) -> ThirdPartyMcpServer | None: ...

    async def save_server(self, server: ThirdPartyMcpServer) -> None: ...

    async def delete_server(self, name: str) -> None: ...

    async def is_agent_enabled(self, name: str, agent_key: str) -> bool: ...


class McpRegistrar(Protocol):
    """The corridor registration boundary. `register` mirrors corridor's
    own `AgentToolServerRegistry.register` never-raise-on-connection-failure
    convention (returns an error string), but a `ValueError` -- the same
    `base_url` already registered by a *different* owner -- is a real
    authoring conflict corridor deliberately raises rather than swallows,
    so `TelephonepoleService` must be ready to catch it."""

    async def register(
        self, name: str, base_url: str, agent_allowed: AgentAllowedCheck
    ) -> str | None: ...

    def unregister(self, base_url: str) -> None: ...


class TelephonepoleService:
    def __init__(self, repository: ServerRepository, *, registrar: McpRegistrar) -> None:
        self._repository = repository
        self._registrar = registrar

    async def list_servers(self) -> tuple[ThirdPartyMcpServer, ...]:
        return await self._repository.list_servers()

    async def add_server(self, name: str, base_url: str) -> str | None:
        """Registers `base_url` with corridor and, only on success,
        persists it under `name`. Returns an error string on failure (a
        `name` already used by this cog, a corridor-side registration
        failure, or `base_url` already owned by a different cog), never
        raises -- same never-raise convention corridor's own registry
        documents."""

        existing = await self._repository.get_server(name)
        if existing is not None:
            return (
                f"a server named {name!r} is already registered "
                f"(base_url: {existing.base_url!r}); remove it first to replace it"
            )

        try:
            error = await self._registrar.register(name, base_url, self._agent_allowed_for(name))
        except ValueError as exc:
            return str(exc)
        if error is not None:
            return error

        await self._repository.save_server(ThirdPartyMcpServer(name=name, base_url=base_url))
        return None

    async def remove_server(self, name: str) -> str | None:
        """Returns an error string if `name` isn't registered, never
        raises."""

        server = await self._repository.get_server(name)
        if server is None:
            return f"no server named {name!r} is registered"
        self._registrar.unregister(server.base_url)
        await self._repository.delete_server(name)
        return None

    async def restore_all(self) -> dict[str, str]:
        """Re-registers every persisted server with corridor -- corridor's
        in-memory `AgentToolServerRegistry` does not survive a bot restart
        even though this cog's own Config does, so `cog_load` calls this
        once. Returns `{name: error}` for any server that failed to
        re-register (its persisted entry is left in place either way --
        the bot owner can retry once the issue is fixed), never raises."""

        errors: dict[str, str] = {}
        for server in await self._repository.list_servers():
            try:
                error = await self._registrar.register(
                    server.name, server.base_url, self._agent_allowed_for(server.name)
                )
            except ValueError as exc:
                error = str(exc)
            if error is not None:
                errors[server.name] = error
        return errors

    def _agent_allowed_for(self, name: str) -> AgentAllowedCheck:
        async def check(agent_key: str) -> bool:
            return await self._repository.is_agent_enabled(name, agent_key)

        return check


__all__ = ["AgentAllowedCheck", "McpRegistrar", "ServerRepository", "TelephonepoleService"]
