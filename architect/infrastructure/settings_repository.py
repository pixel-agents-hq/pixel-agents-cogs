"""Red Config-backed implementation of architect's settings storage.

Global (bot-owner) scope only -- unlike pico, architect has no per-guild
`enabled` toggle: its A2A listener is process-scoped, not per-guild (see
docs/architect-design.md section 6). The LLM *connection* lives in
corridor, shared with pico. architect's office layout and webview/
WebSocket hosting no longer live here at all -- the layout is the
"editor" aggregate in pixelagents' `OfficeStateFacade`, and `cctv` is the
only cog serving a dashboard page (docs/cctv-design.md).
"""

from __future__ import annotations

from typing import Any, cast

from redbot.core import Config

from ..domain import GlobalSettings

# Freshly rolled for this cog after retiring the ws_host/ws_port/layout
# fields below -- the previous identifier (4172636869746374) is dead,
# not reused, so a pre-cctv install's stale WebSocket config/legacy
# layout is simply never read again rather than needing a migration.
CONFIG_IDENTIFIER = 7440228423

DEFAULT_MAX_TOOL_CALLS = 5
DEFAULT_SYSTEM_PROMPT = (
    "You are Architect, an assistant reachable only through the A2A protocol -- "
    "no Discord user ever talks to you directly. Another agent (Pico) has "
    "delegated a task to you. "
    "You maintain exactly one persistent office layout -- tiles, zones, "
    "furniture, and seats -- not a searchable library of named layouts, just "
    "your own current one. describe_office, describe_tiles, and find_furniture "
    "show you its current state and take no layout name or id, since there is "
    "only ever the one; call them whenever a request is about 'the layout' or "
    "'the office' without assuming you need more information first. "
    "paint_tiles, place_furniture, move_furniture, remove_furniture, and "
    "create_zone edit it directly and persist immediately. "
    "Use the tools you're given if they help, then reply with your final "
    "answer as plain text; that text is sent back directly, so make it "
    "complete and self-contained."
)
# Off by default -- verbose per-tool-call logging (tool name, arguments,
# and result/error for every call the LLM makes) is noisy in normal
# operation and only useful while actively diagnosing a tool-calling
# issue. Logged at INFO (`tool_loop_service.py`), not DEBUG, so it's
# visible under Red's default console/file log level without needing the
# process-wide `--debug` startup flag -- this flag is the only way to
# gate it, since Red has no per-cog runtime log-level command.
DEFAULT_DEBUG_LOGGING = False

GLOBAL_DEFAULTS: dict[str, object] = {
    "max_tool_calls": DEFAULT_MAX_TOOL_CALLS,
    "system_prompt": DEFAULT_SYSTEM_PROMPT,
    "debug_logging": DEFAULT_DEBUG_LOGGING,
}


class RedArchitectRepository:
    """The typed boundary around this cog's Red Config storage."""

    def __init__(self, config: Any) -> None:
        self._config = config

    @classmethod
    def create(cls, cog: object) -> RedArchitectRepository:
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
    "DEFAULT_SYSTEM_PROMPT",
    "GLOBAL_DEFAULTS",
    "RedArchitectRepository",
]
