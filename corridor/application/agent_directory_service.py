"""In-process registry of A2A-reachable agents, mounted on corridor's own
shared A2A listener (`corridor/infrastructure/a2a_server.py`). Depends on
nothing but the domain module it stores -- same
register/unregister_owner/unregister/list shape
`tool_registry_service.py`'s `ToolRegistryService` already follows. See
docs/agent-directory-design.md for the full design rationale.
"""

from __future__ import annotations

from ..domain.agent_directory import RegisteredAgent


class AgentDirectoryService:
    """One directory per bot process, not per guild -- same scoping as
    ToolRegistryService/EventBusService."""

    def __init__(self) -> None:
        self._agents: dict[str, tuple[str, RegisteredAgent]] = {}

    def register(self, agent: RegisteredAgent, *, owner: str) -> None:
        """Register `agent` under `owner` (the registering cog's class
        name, matching `subscribe_event`'s convention). Re-registering
        the same `agent_key` under the same `owner` overwrites --
        idempotent across repeat `cog_load` calls. A name collision from
        a *different* owner is a real authoring conflict between two
        cogs, so it raises instead of silently letting one shadow the
        other -- same collision policy as `ToolRegistryService.register`.
        """

        existing = self._agents.get(agent.agent_key)
        if existing is not None and existing[0] != owner:
            raise ValueError(
                f"agent {agent.agent_key!r} is already registered by {existing[0]!r}, "
                f"cannot re-register it for {owner!r}"
            )
        self._agents[agent.agent_key] = (owner, agent)

    def unregister_owner(self, owner: str) -> None:
        """The registering cog's own responsibility, called from its own
        cog_unload -- same convention as
        `ToolRegistryService.unregister_owner`."""

        for agent_key in [k for k, (o, _) in self._agents.items() if o == owner]:
            del self._agents[agent_key]

    def unregister(self, agent_key: str) -> None:
        """Remove one agent by key, regardless of owner. A no-op if
        `agent_key` isn't registered."""

        self._agents.pop(agent_key, None)

    def list_agents(self) -> tuple[RegisteredAgent, ...]:
        return tuple(agent for _, agent in self._agents.values())


__all__ = ["AgentDirectoryService"]
