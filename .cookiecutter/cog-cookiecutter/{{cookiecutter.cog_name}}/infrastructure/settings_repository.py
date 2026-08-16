"""Red Config-backed implementation of the CounterRepository protocol."""

from __future__ import annotations

import asyncio
from typing import Any, cast

from redbot.core import Config

# Filled in by hooks/post_gen_project.py with a freshly rolled random int.
# Config keys and scopes are the canonical registration contract once real
# data exists under this identifier -- do not change casually after release.
CONFIG_IDENTIFIER = __CONFIG_IDENTIFIER__

GUILD_DEFAULTS: dict[str, object] = {
    "count": 0,
}


class RedCounterRepository:
    """The typed boundary around this cog's Red Config storage."""

    def __init__(self, config: Any) -> None:
        self._config = config
        self._lock = asyncio.Lock()

    @classmethod
    def create(cls, cog: object) -> RedCounterRepository:
        config = Config.get_conf(
            cog,
            identifier=CONFIG_IDENTIFIER,
            force_registration=True,
        )
        config.register_guild(**GUILD_DEFAULTS)
        return cls(config)

    @property
    def config(self) -> Any:
        """Expose the raw Config object for the legacy cog compatibility surface."""

        return self._config

    async def read(self, guild_id: int) -> int:
        return cast(int, await self._config.guild_from_id(guild_id).count())

    async def increment(self, guild_id: int) -> int:
        guild = self._config.guild_from_id(guild_id)
        async with self._lock:
            count = cast(int, await guild.count()) + 1
            await guild.count.set(count)
        return count
