"""Red Config-backed implementation of the SwitchboardSettings protocol."""

from __future__ import annotations

from typing import Any, cast

from redbot.core import Config

# Filled in by hooks/post_gen_project.py with a freshly rolled random int.
# Config keys and scopes are the canonical registration contract once real
# data exists under this identifier -- do not change casually after release.
CONFIG_IDENTIFIER = 9207945584

GLOBAL_DEFAULTS: dict[str, object] = {
    # Cogs switchboard loaded and should keep loaded (see repair()).
    "managed": [],
    "autorepair": True,
}


class RedSwitchboardSettings:
    """The typed boundary around this cog's Red Config storage."""

    def __init__(self, config: Any) -> None:
        self._config = config

    @classmethod
    def create(cls, cog: object) -> RedSwitchboardSettings:
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

    async def managed(self) -> list[str]:
        return list(cast(list[str], await self._config.managed()))

    async def set_managed(self, names: list[str]) -> None:
        await self._config.managed.set(sorted(set(names)))

    async def autorepair(self) -> bool:
        return bool(await self._config.autorepair())

    async def set_autorepair(self, enabled: bool) -> None:
        await self._config.autorepair.set(enabled)
