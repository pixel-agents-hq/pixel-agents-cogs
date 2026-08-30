"""Red Config-backed implementation of painter's settings storage.

Global (bot-owner) scope only, mirroring architect's own settings
repository shape (`architect/infrastructure/settings_repository.py`) --
painter is A2A-only too, with no per-guild `enabled` toggle. Unlike
architect, painter owns no `ws_host`/`ws_port`/webview settings at all --
it never serves a WebSocket transport or a Dashboard page of its own, and
its office layout isn't its own private store either (it reads/writes the
one pixelagents-owned layout, see `docs/painter-design.md` part A). The
LLM *connection* lives in corridor, shared with pico and architect.
"""

from __future__ import annotations

from typing import Any, cast

from redbot.core import Config

from ..domain import GlobalSettings

# Filled in by hooks/post_gen_project.py with a freshly rolled random int.
# Config keys and defaults below are the canonical registration contract
# once real data exists under this identifier; do not change casually
# after release.
CONFIG_IDENTIFIER = 7611268362

DEFAULT_MAX_TOOL_CALLS = 5
DEFAULT_SYSTEM_PROMPT = (
    "You are Painter, an assistant reachable only through the A2A protocol -- "
    "no Discord user ever talks to you directly. Another agent (Pico) has "
    "delegated a task to you. "
    "You share one persistent office layout with another agent, Architect: "
    "Architect knows what tiles, walls, and furniture exist and where -- it "
    "is colorblind and has no notion of color at all. You are the color "
    "specialist: you can read and change the color of floor tiles, walls, "
    "and furniture, but you can never add, remove, move, or otherwise "
    "restructure anything. "
    "Use consult_architect to ask what's in the layout and where (kinds, "
    "positions, styles) whenever a request needs that -- phrase it as an "
    "explicit instruction, since Architect only acts on what you state, not "
    "on a goal or rationale mentioned alongside it. "
    "There is no fixed list of color names -- you have full control over "
    "hue, saturation, brightness, and contrast (or a hex shorthand), and "
    "you translate whatever color someone describes into those terms "
    "yourself: a plain color word ('blue', 'gold', 'white', 'grey'), a "
    "hex code, or a request like 'a lighter shade' or 'more muted'. Use "
    "describe_tile_colors/describe_furniture_colors first to see a "
    "region/item's *current* exact color (reported the same way) before "
    "adjusting it lighter, darker, more saturated, or otherwise relative "
    "to what's already there. Use recolor_tiles/recolor_furniture/"
    "recolor_furniture_by_style to apply a color -- these edit the shared "
    "layout directly and persist immediately. "
    "Use the tools you're given if they help, then reply with your final "
    "answer as plain text; that text is sent back directly, so make it "
    "complete and self-contained."
)
# Off by default -- verbose per-tool-call logging (tool name, arguments,
# and result/error for every call the LLM makes) is noisy in normal
# operation and only useful while actively diagnosing a tool-calling
# issue. Same convention as architect's own `debug_logging`.
DEFAULT_DEBUG_LOGGING = False

GLOBAL_DEFAULTS: dict[str, object] = {
    "max_tool_calls": DEFAULT_MAX_TOOL_CALLS,
    "system_prompt": DEFAULT_SYSTEM_PROMPT,
    "debug_logging": DEFAULT_DEBUG_LOGGING,
}


class RedPainterRepository:
    """The typed boundary around this cog's Red Config storage."""

    def __init__(self, config: Any) -> None:
        self._config = config

    @classmethod
    def create(cls, cog: object) -> RedPainterRepository:
        config = Config.get_conf(cog, identifier=CONFIG_IDENTIFIER, force_registration=True)
        config.register_global(**GLOBAL_DEFAULTS)
        return cls(config)

    @property
    def config(self) -> Any:
        """Expose the raw Config object for the legacy cog compatibility surface."""

        return self._config

    async def global_settings(self) -> GlobalSettings:
        return GlobalSettings(
            max_tool_calls=cast(int, await self._config.max_tool_calls()),
            system_prompt=cast(str, await self._config.system_prompt()),
            debug_logging=cast(bool, await self._config.debug_logging()),
        )

    async def set_max_tool_calls(self, value: int) -> None:
        if isinstance(value, bool) or value < 1:
            raise ValueError("Max tool calls must be a positive integer.")
        await self._config.max_tool_calls.set(value)

    async def set_system_prompt(self, value: str) -> None:
        await self._config.system_prompt.set(value)

    async def reset_system_prompt(self) -> None:
        await self._config.system_prompt.set(DEFAULT_SYSTEM_PROMPT)

    async def set_debug_logging(self, value: bool) -> None:
        await self._config.debug_logging.set(bool(value))


__all__ = [
    "CONFIG_IDENTIFIER",
    "DEFAULT_DEBUG_LOGGING",
    "DEFAULT_MAX_TOOL_CALLS",
    "DEFAULT_SYSTEM_PROMPT",
    "GLOBAL_DEFAULTS",
    "RedPainterRepository",
]
