"""Red Config-backed storage for whether a registered LLM tool is
currently visible -- a global default plus an optional per-guild override.

Keyed by `RegisteredTool.name` (e.g. `deskutils_time`, `other_greet`), not
the space-separated Discord command qualified name selection.py uses --
this is the same string corridor's visibility filter predicate receives on
every `list_tools_for` call, so no reverse lookup is needed at filter time.

Separate Config keys from tool_selection_repository.py's (selection is "can
this ever be a tool"; this is "is it visible right now") but the same
`Config.get_conf(cog, identifier=CONFIG_IDENTIFIER, ...)` singleton -- see
that module's docstring for why sharing the identifier across repository
classes is safe.

Global-default-plus-guild-override, not global-only: unlike Node.js host
state or the selection set above, *visibility* is genuinely a per-guild
decision (docs/toolbox-command-tool-toggle-design.md) -- this is the first
toolbox Config concern to use `register_guild` at all.
"""

from __future__ import annotations

from typing import Any, cast

from redbot.core import Config

from .settings_repository import CONFIG_IDENTIFIER

GLOBAL_DEFAULTS: dict[str, object] = {
    "tool_enabled_default": {},
}
GUILD_DEFAULTS: dict[str, object] = {
    "tool_enabled_override": {},
}


class RedToolVisibilityRepository:
    """The typed boundary around this cog's tool-visibility Config keys."""

    def __init__(self, config: Any) -> None:
        self._config = config

    @classmethod
    def create(cls, cog: object) -> RedToolVisibilityRepository:
        config = Config.get_conf(
            cog,
            identifier=CONFIG_IDENTIFIER,
            force_registration=True,
        )
        config.register_global(**GLOBAL_DEFAULTS)
        config.register_guild(**GUILD_DEFAULTS)
        return cls(config)

    async def get_default(self, tool_name: str) -> bool | None:
        defaults = cast("dict[str, bool]", await self._config.tool_enabled_default())
        return defaults.get(tool_name)

    async def set_default(self, tool_name: str, enabled: bool) -> None:
        defaults = cast("dict[str, bool]", await self._config.tool_enabled_default())
        defaults = dict(defaults)
        defaults[tool_name] = enabled
        await self._config.tool_enabled_default.set(defaults)

    async def all_defaults(self) -> dict[str, bool]:
        return cast("dict[str, bool]", await self._config.tool_enabled_default())

    async def all_overrides(self, guild_id: int) -> dict[str, bool]:
        guild_config = self._config.guild_from_id(guild_id)
        return cast("dict[str, bool]", await guild_config.tool_enabled_override())

    async def get_override(self, guild_id: int, tool_name: str) -> bool | None:
        guild_config = self._config.guild_from_id(guild_id)
        overrides = cast("dict[str, bool]", await guild_config.tool_enabled_override())
        return overrides.get(tool_name)

    async def set_override(self, guild_id: int, tool_name: str, enabled: bool) -> None:
        guild_config = self._config.guild_from_id(guild_id)
        overrides = cast("dict[str, bool]", await guild_config.tool_enabled_override())
        overrides = dict(overrides)
        overrides[tool_name] = enabled
        await guild_config.tool_enabled_override.set(overrides)

    async def clear_override(self, guild_id: int, tool_name: str) -> None:
        guild_config = self._config.guild_from_id(guild_id)
        overrides = cast("dict[str, bool]", await guild_config.tool_enabled_override())
        overrides = dict(overrides)
        overrides.pop(tool_name, None)
        await guild_config.tool_enabled_override.set(overrides)
