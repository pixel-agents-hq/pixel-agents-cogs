"""Red Config-backed implementation of the `AgentRepository` protocol
(`application/service.py`). Global (bot-wide), never per-guild -- corridor's
`AgentDirectoryService` is process-wide, so a registered agent's own
settings must be too, same rationale telephonepole's
`RedTelephonepoleRepository` documents for its own server registry.
"""

from __future__ import annotations

from typing import Any, cast

from redbot.core import Config

from ..domain import DEFAULT_MAX_TOOL_CALLS, DEFAULT_PERMISSION_GROUP, CustomAgent

# Filled in by hooks/post_gen_project.py with a freshly rolled random int.
# Config keys and scopes are the canonical registration contract once real
# data exists under this identifier -- do not change casually after release.
CONFIG_IDENTIFIER = 3259522800

GLOBAL_DEFAULTS: dict[str, object] = {
    # agent_key -> {system_prompt, permission_group, max_tool_calls,
    # debug_logging, request_timeout_seconds}
    "agents": {},
}


class RedBootcampRepository:
    """The typed boundary around this cog's Red Config storage."""

    def __init__(self, config: Any) -> None:
        self._config = config

    @classmethod
    def create(cls, cog: object) -> RedBootcampRepository:
        config = Config.get_conf(
            cog,
            identifier=CONFIG_IDENTIFIER,
            force_registration=True,
        )
        config.register_global(**GLOBAL_DEFAULTS)
        return cls(config)

    @property
    def config(self) -> Any:
        """Expose the raw Config object for the legacy cog compatibility surface."""

        return self._config

    async def list_agents(self) -> tuple[CustomAgent, ...]:
        raw = cast("dict[str, dict[str, object]]", await self._config.agents())
        return tuple(_agent_from_raw(agent_key, data) for agent_key, data in raw.items())

    async def get_agent(self, agent_key: str) -> CustomAgent | None:
        raw = cast("dict[str, dict[str, object]]", await self._config.agents())
        data = raw.get(agent_key)
        if data is None:
            return None
        return _agent_from_raw(agent_key, data)

    async def save_agent(self, agent: CustomAgent) -> None:
        agents = dict(cast("dict[str, dict[str, object]]", await self._config.agents()))
        agents[agent.agent_key] = {
            "system_prompt": agent.system_prompt,
            "permission_group": agent.permission_group,
            "max_tool_calls": agent.max_tool_calls,
            "debug_logging": agent.debug_logging,
            "request_timeout_seconds": agent.request_timeout_seconds,
        }
        await self._config.agents.set(agents)

    async def delete_agent(self, agent_key: str) -> None:
        agents = dict(cast("dict[str, dict[str, object]]", await self._config.agents()))
        agents.pop(agent_key, None)
        await self._config.agents.set(agents)


def _agent_from_raw(agent_key: str, data: dict[str, object]) -> CustomAgent:
    return CustomAgent(
        agent_key=agent_key,
        system_prompt=cast(str, data["system_prompt"]),
        permission_group=cast(str, data.get("permission_group", DEFAULT_PERMISSION_GROUP)),
        max_tool_calls=cast(int, data.get("max_tool_calls", DEFAULT_MAX_TOOL_CALLS)),
        debug_logging=cast(bool, data.get("debug_logging", False)),
        request_timeout_seconds=cast("float | None", data.get("request_timeout_seconds")),
    )


__all__ = ["CONFIG_IDENTIFIER", "GLOBAL_DEFAULTS", "RedBootcampRepository"]
