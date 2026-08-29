"""Red Config-backed settings storage -- everything here is global (bot-
wide), never per-guild: neither an external MCP client nor a registered
A2A agent's own call carries guild context, and the per-agent MCP-access
toggle is deliberately process-wide, not per-guild. See
docs/suggestionbox-design.md §2/§3.
"""

from __future__ import annotations

from typing import Any, cast

from redbot.core import Config

# Filled in by hooks/post_gen_project.py with a freshly rolled random int.
# Config keys and scopes are the canonical registration contract once real
# data exists under this identifier -- do not change casually after release.
CONFIG_IDENTIFIER = 7942184802

GLOBAL_DEFAULTS: dict[str, object] = {
    "mcp_host": "127.0.0.1",
    "mcp_port": 8934,
    "feedback_guild_id": None,
    "feedback_channel_id": None,
    "mcp_enabled_agents": {},
}


class RedSuggestionboxRepository:
    """The typed boundary around this cog's Red Config storage."""

    def __init__(self, config: Any) -> None:
        self._config = config

    @classmethod
    def create(cls, cog: object) -> RedSuggestionboxRepository:
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

    async def mcp_listener(self) -> tuple[str, int]:
        return (
            cast(str, await self._config.mcp_host()),
            cast(int, await self._config.mcp_port()),
        )

    async def set_mcp_host(self, value: str) -> None:
        await self._config.mcp_host.set(value)

    async def set_mcp_port(self, value: int) -> None:
        await self._config.mcp_port.set(value)

    async def feedback_channel(self) -> tuple[int, int] | None:
        guild_id = cast("int | None", await self._config.feedback_guild_id())
        channel_id = cast("int | None", await self._config.feedback_channel_id())
        if guild_id is None or channel_id is None:
            return None
        return (guild_id, channel_id)

    async def set_feedback_channel(self, guild_id: int, channel_id: int) -> None:
        await self._config.feedback_guild_id.set(guild_id)
        await self._config.feedback_channel_id.set(channel_id)

    async def is_agent_enabled(self, agent_key: str) -> bool:
        """Off by default for an agent with no explicit entry yet -- see
        docs/suggestionbox-design.md §2 on why a newly-registered agent
        starts closed rather than open."""

        enabled = cast("dict[str, bool]", await self._config.mcp_enabled_agents())
        return bool(enabled.get(agent_key, False))

    async def set_agent_enabled(self, agent_key: str, value: bool) -> None:
        enabled = dict(cast("dict[str, bool]", await self._config.mcp_enabled_agents()))
        enabled[agent_key] = value
        await self._config.mcp_enabled_agents.set(enabled)

    async def all_agent_settings(self) -> dict[str, bool]:
        return cast("dict[str, bool]", await self._config.mcp_enabled_agents())


__all__ = ["CONFIG_IDENTIFIER", "GLOBAL_DEFAULTS", "RedSuggestionboxRepository"]
