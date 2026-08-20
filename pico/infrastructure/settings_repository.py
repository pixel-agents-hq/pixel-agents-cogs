"""Red Config-backed implementation of Pico's settings storage.

Owner (global) settings hold the LLM connection -- endpoint, virtual key,
model, per-turn tool-call budget, system prompt. Guild settings hold only
the per-server `enabled` toggle, mirroring floorplan's convention that the
LLM *connection* is bot-owner scope while turning the feature on for a
given server is admin scope.
"""

from __future__ import annotations

from typing import Any, cast

from redbot.core import Config

from ..domain import GlobalSettings, GuildSettings

# Freshly rolled for this cog -- Config keys, scopes, and defaults below are
# the canonical registration contract once real data exists under this
# identifier; do not change casually after release.
CONFIG_IDENTIFIER = 6416697433

DEFAULT_LLM_BASE_URL = "https://litellm.nntin.xyz/"
DEFAULT_MAX_TOOL_CALLS = 5
DEFAULT_SYSTEM_PROMPT = (
    "You are Pico, a helpful presence in this Discord server. You were just "
    "gated in to respond to a message -- reply naturally and concisely. "
    "You can only act by calling the tools you're given; you have no other "
    "way to reach Discord, so if you want to say anything, call the reply "
    "tool. If nothing useful needs to be said, simply don't call any tool."
)

GLOBAL_DEFAULTS: dict[str, object] = {
    "llm_base_url": DEFAULT_LLM_BASE_URL,
    "llm_api_key": None,
    "llm_model": None,
    "max_tool_calls": DEFAULT_MAX_TOOL_CALLS,
    "system_prompt": DEFAULT_SYSTEM_PROMPT,
}
GUILD_DEFAULTS: dict[str, object] = {
    "enabled": False,
}


class RedPicoRepository:
    """The typed boundary around this cog's Red Config storage."""

    def __init__(self, config: Any) -> None:
        self._config = config

    @classmethod
    def create(cls, cog: object) -> RedPicoRepository:
        config = Config.get_conf(cog, identifier=CONFIG_IDENTIFIER, force_registration=True)
        config.register_global(**GLOBAL_DEFAULTS)
        config.register_guild(**GUILD_DEFAULTS)
        return cls(config)

    @property
    def config(self) -> Any:
        """Expose the raw Config object for the legacy cog compatibility surface."""

        return self._config

    async def global_settings(self) -> GlobalSettings:
        return GlobalSettings(
            llm_base_url=cast(str, await self._config.llm_base_url()),
            llm_api_key=cast("str | None", await self._config.llm_api_key()),
            llm_model=cast("str | None", await self._config.llm_model()),
            max_tool_calls=cast(int, await self._config.max_tool_calls()),
            system_prompt=cast(str, await self._config.system_prompt()),
        )

    async def guild_settings(self, guild_id: int) -> GuildSettings:
        enabled = cast(bool, await self._config.guild_from_id(guild_id).enabled())
        return GuildSettings(guild_id=guild_id, enabled=enabled)

    async def guild_enabled(self, guild_id: int) -> bool:
        return cast(bool, await self._config.guild_from_id(guild_id).enabled())

    async def set_llm_base_url(self, value: str) -> None:
        await self._config.llm_base_url.set(value)

    async def set_llm_api_key(self, value: str) -> None:
        await self._config.llm_api_key.set(value)

    async def set_llm_model(self, value: str) -> None:
        await self._config.llm_model.set(value)

    async def set_max_tool_calls(self, value: int) -> None:
        if isinstance(value, bool) or value < 1:
            raise ValueError("Max tool calls must be a positive integer.")
        await self._config.max_tool_calls.set(value)

    async def set_system_prompt(self, value: str) -> None:
        await self._config.system_prompt.set(value)

    async def reset_system_prompt(self) -> None:
        await self._config.system_prompt.set(DEFAULT_SYSTEM_PROMPT)

    async def set_guild_enabled(self, guild_id: int, value: bool) -> None:
        await self._config.guild_from_id(guild_id).enabled.set(value)
