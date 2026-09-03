"""Framework-agnostic application logic for creating/removing/editing
dynamically-created custom agents, and registering/unregistering each one
with corridor's `AgentDirectoryService`.

Depends only on the `AgentRepository` and `AgentRegistrar` protocols below,
never on corridor/redbot/discord.py directly -- the same
"corridor-agnostic business logic, corridor-aware adapter" split
`telephonepole/application/service.py`'s `TelephonepoleService`/
`McpRegistrar` already use for its own corridor integration. The real
`AgentRegistrar` implementation (`adapters/cog_base.py`'s
`CorridorAgentRegistrar`) is the only place that ever imports
`corridor.domain.RegisteredAgent`, `corridor.domain.agent_executor`, or
calls `corridor.register_agent`/`unregister_agent`.
"""

from __future__ import annotations

import re
from dataclasses import replace
from typing import Protocol

from ..domain import DEFAULT_MAX_TOOL_CALLS, DEFAULT_PERMISSION_GROUP, CustomAgent

# Subcommand names `adapters/commands.py`'s `[p]bootcamp <subcommand>`
# already reserves -- an agent_key colliding with one of these would make
# `[p]bootcamp <agent_key> <prompt>` ambiguous with e.g. `[p]bootcamp
# remove`, so `create_agent` rejects them up front rather than leaving that
# ambiguity for Discord's own command dispatch to resolve arbitrarily.
RESERVED_AGENT_KEYS = frozenset(
    {"create", "remove", "list", "permission", "maxtoolcalls", "debuglogging"}
)

_AGENT_KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")


class AgentRepository(Protocol):
    """The persistence boundary `BootcampService` depends on."""

    async def list_agents(self) -> tuple[CustomAgent, ...]: ...

    async def get_agent(self, agent_key: str) -> CustomAgent | None: ...

    async def save_agent(self, agent: CustomAgent) -> None: ...

    async def delete_agent(self, agent_key: str) -> None: ...


class AgentRegistrar(Protocol):
    """The corridor registration boundary. `register` mirrors corridor's
    own `AgentDirectoryService.register` never-raise-on-a-fresh-registration
    convention (returns an error string for anything else), but a
    `ValueError` -- `agent_key` already registered by a *different* owner
    -- is a real authoring conflict corridor deliberately raises rather
    than swallows, so `BootcampService` must be ready to catch it. Called
    again with the same `agent_key` to apply an edit (e.g.
    `set_permission_group`) -- idempotent for the same owner, per
    `AgentDirectoryService.register`'s own contract."""

    async def register(self, agent: CustomAgent) -> str | None: ...

    async def unregister(self, agent_key: str) -> None: ...


class BootcampService:
    def __init__(self, repository: AgentRepository, *, registrar: AgentRegistrar) -> None:
        self._repository = repository
        self._registrar = registrar

    async def list_agents(self) -> tuple[CustomAgent, ...]:
        return await self._repository.list_agents()

    async def get_agent(self, agent_key: str) -> CustomAgent | None:
        return await self._repository.get_agent(agent_key)

    async def create_agent(
        self,
        agent_key: str,
        system_prompt: str,
        *,
        permission_group: str = DEFAULT_PERMISSION_GROUP,
        max_tool_calls: int = DEFAULT_MAX_TOOL_CALLS,
        debug_logging: bool = False,
    ) -> str | None:
        """Registers a fresh `CustomAgent` with corridor and, only on
        success, persists it. Returns an error string on failure (an
        invalid/reserved/already-used `agent_key`, an empty prompt, or a
        corridor-side registration failure), never raises -- same
        never-raise convention corridor's own directory documents."""

        if not _AGENT_KEY_PATTERN.match(agent_key):
            return (
                f"invalid agent_key {agent_key!r}: it must start with a lowercase letter and "
                "contain only lowercase letters, digits, and underscores"
            )
        if agent_key in RESERVED_AGENT_KEYS:
            return f"{agent_key!r} is a reserved bootcamp subcommand name; choose a different one"
        if not system_prompt.strip():
            return "system_prompt must not be empty"
        if isinstance(max_tool_calls, bool) or max_tool_calls < 1:
            return "max_tool_calls must be a positive integer"
        existing = await self._repository.get_agent(agent_key)
        if existing is not None:
            return (
                f"a custom agent named {agent_key!r} already exists; remove it first to replace it"
            )

        agent = CustomAgent(
            agent_key=agent_key,
            system_prompt=system_prompt,
            permission_group=permission_group,
            max_tool_calls=max_tool_calls,
            debug_logging=debug_logging,
        )
        error = await self._register(agent)
        if error is not None:
            return error
        await self._repository.save_agent(agent)
        return None

    async def remove_agent(self, agent_key: str) -> str | None:
        """Returns an error string if `agent_key` isn't a known custom
        agent, never raises."""

        existing = await self._repository.get_agent(agent_key)
        if existing is None:
            return f"no custom agent named {agent_key!r} exists"
        await self._registrar.unregister(agent_key)
        await self._repository.delete_agent(agent_key)
        return None

    async def set_permission_group(self, agent_key: str, permission_group: str) -> str | None:
        """Edits which corridor permission group gates *use* of this agent,
        both directly and through pico -- re-registers with corridor so its
        stored `RegisteredAgent.required_permission_group` reflects the
        change immediately (unlike `max_tool_calls`/`debug_logging` below,
        which the running executor reads fresh from `AgentRepository` every
        turn and so need no re-registration)."""

        agent = await self._repository.get_agent(agent_key)
        if agent is None:
            return f"no custom agent named {agent_key!r} exists"
        updated = replace(agent, permission_group=permission_group)
        error = await self._register(updated)
        if error is not None:
            return error
        await self._repository.save_agent(updated)
        return None

    async def set_max_tool_calls(self, agent_key: str, max_tool_calls: int) -> str | None:
        agent = await self._repository.get_agent(agent_key)
        if agent is None:
            return f"no custom agent named {agent_key!r} exists"
        if isinstance(max_tool_calls, bool) or max_tool_calls < 1:
            return "max_tool_calls must be a positive integer"
        await self._repository.save_agent(replace(agent, max_tool_calls=max_tool_calls))
        return None

    async def set_debug_logging(self, agent_key: str, debug_logging: bool) -> str | None:
        agent = await self._repository.get_agent(agent_key)
        if agent is None:
            return f"no custom agent named {agent_key!r} exists"
        await self._repository.save_agent(replace(agent, debug_logging=bool(debug_logging)))
        return None

    async def restore_all(self) -> dict[str, str]:
        """Re-registers every persisted agent with corridor -- corridor's
        in-memory `AgentDirectoryService` does not survive a bot restart
        even though this cog's own Config does, so `cog_load` calls this
        once. Returns `{agent_key: error}` for any agent that failed to
        re-register (its persisted entry is left in place either way --
        the bot owner can retry once the issue is fixed), never raises."""

        errors: dict[str, str] = {}
        for agent in await self._repository.list_agents():
            error = await self._register(agent)
            if error is not None:
                errors[agent.agent_key] = error
        return errors

    async def _register(self, agent: CustomAgent) -> str | None:
        try:
            return await self._registrar.register(agent)
        except ValueError as exc:
            return str(exc)


__all__ = ["RESERVED_AGENT_KEYS", "AgentRegistrar", "AgentRepository", "BootcampService"]
