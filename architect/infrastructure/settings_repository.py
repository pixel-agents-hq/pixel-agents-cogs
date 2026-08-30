"""Red Config-backed implementation of architect's settings storage.

Global (bot-owner) scope only -- unlike pico, architect has no per-guild
`enabled` toggle: its A2A listener and webview are process-scoped, not
per-guild (see docs/architect-design.md section 6). The LLM *connection*
lives in corridor, shared with pico.
"""

from __future__ import annotations

from typing import Any, cast

from redbot.core import Config

from ..domain import GlobalSettings

# Freshly rolled for this cog -- Config keys and defaults below are the
# canonical registration contract once real data exists under this
# identifier; do not change casually after release.
CONFIG_IDENTIFIER = 4172636869746374

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
# Deliberately different from floorplan's own ws_port default (3210) and
# corridor's shared a2a_port default (8931) -- independent listeners on
# one host.
DEFAULT_WS_HOST = "127.0.0.1"
DEFAULT_WS_PORT = 8932
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
    "ws_host": DEFAULT_WS_HOST,
    "ws_port": DEFAULT_WS_PORT,
    "debug_logging": DEFAULT_DEBUG_LOGGING,
    # Legacy location only -- the live layout store moved to
    # `pixelagents.infrastructure.office_layout_settings` (docs/painter-design.md
    # part A) so `painter` can reach it too. Kept registered here, with
    # `layout()` still readable below, purely so `CogBase`'s one-time
    # migration can read whatever an existing install still has under this
    # old key and copy it across; nothing writes here anymore.
    "layout": None,
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
            ws_host=cast(str, await self._config.ws_host()),
            ws_port=cast(int, await self._config.ws_port()),
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

    async def set_ws_host(self, value: str) -> None:
        await self._config.ws_host.set(value)

    async def set_ws_port(self, value: int) -> None:
        if isinstance(value, bool) or not 1 <= value <= 65535:
            raise ValueError("Port must be an integer from 1 through 65535.")
        await self._config.ws_port.set(value)

    async def set_debug_logging(self, value: bool) -> None:
        await self._config.debug_logging.set(bool(value))

    async def legacy_layout(self) -> dict[str, object] | None:
        """Whatever this install last stored under the old, architect-owned
        `layout` key -- migration-only, see `CogBase._migrate_legacy_layout`.
        `None` for a fresh install, or one already migrated."""

        return cast("dict[str, object] | None", await self._config.layout())

    async def clear_legacy_layout(self) -> None:
        await self._config.layout.set(None)
