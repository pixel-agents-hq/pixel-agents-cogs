"""Red Config-backed storage for which commands the bot owner has opted
into LLM-tool wrapping.

Deliberately separate from RedNodeRepository/settings_repository.py's
CONFIG_IDENTIFIER-bearing keys -- different concern, different lifecycle --
but shares the same `Config.get_conf(cog, identifier=CONFIG_IDENTIFIER, ...)`
call: Red's Config is a singleton per (cog name, identifier) pair
(redbot/core/config.py's ConfigMeta cache), and register_global/
register_guild accumulate keys across separate calls, so two repository
classes each registering their own keys against the same identifier is the
normal way to split one cog's Config surface across files.

Selection is global (bot owner scoped, host-wide -- same reasoning as
RedNodeRepository's own global-only Config, see settings_repository.py's
module docstring): whether a command *can ever* be wrapped as a tool is one
decision, not a per-guild one. Per-guild scope enters at the *visibility*
layer instead (Phase 4), which is deliberately a separate Config
concern -- see docs/toolbox-command-tool-toggle-design.md.
"""

from __future__ import annotations

from typing import Any, cast

from redbot.core import Config

from .settings_repository import CONFIG_IDENTIFIER

GLOBAL_DEFAULTS: dict[str, object] = {
    "selected_tool_commands": [],
}


class RedToolSelectionRepository:
    """The typed boundary around this cog's tool-selection Config keys."""

    def __init__(self, config: Any) -> None:
        self._config = config

    @classmethod
    def create(cls, cog: object) -> RedToolSelectionRepository:
        config = Config.get_conf(
            cog,
            identifier=CONFIG_IDENTIFIER,
            force_registration=True,
        )
        config.register_global(**GLOBAL_DEFAULTS)
        return cls(config)

    async def list_selected(self) -> frozenset[str]:
        raw = cast("list[str]", await self._config.selected_tool_commands())
        return frozenset(raw)

    async def add_selected(self, qualified_name: str) -> None:
        selected = set(await self.list_selected())
        selected.add(qualified_name)
        await self._config.selected_tool_commands.set(sorted(selected))

    async def remove_selected(self, qualified_name: str) -> None:
        selected = set(await self.list_selected())
        selected.discard(qualified_name)
        await self._config.selected_tool_commands.set(sorted(selected))
