"""Red Config-backed implementation of animator's settings storage.

Global (bot-owner) scope only, mirroring painter's/architect's own settings
repository shape -- animator is A2A-only too, with no per-guild `enabled`
toggle and no domain state of its own (its actual capabilities live in
pixel-art-mcp, reached through corridor's MCP bridge). The LLM connection
lives in corridor, shared with pico/architect/painter.
"""

from __future__ import annotations

from typing import Any, cast

from redbot.core import Config

from ..domain import GlobalSettings

# Filled in by hooks/post_gen_project.py with a freshly rolled random int.
# Config keys and defaults below are the canonical registration contract
# once real data exists under this identifier; do not change casually
# after release.
CONFIG_IDENTIFIER = 5863973708

DEFAULT_MAX_TOOL_CALLS = 8
DEFAULT_SYSTEM_PROMPT = (
    "You are Animator, an assistant reachable only through the A2A protocol -- no "
    "Discord user ever talks to you directly. Another agent (Pico) has delegated a "
    "pixel-art modeling/rendering task to you. "
    "Your tools come from pixel-art-mcp: create_project, execute_blender_python, "
    "inspect_scene, render_preview, render_sprites, get_job, cancel_job, "
    "get_artifact, and related project/reference tools. Model in Blender via "
    "execute_blender_python, inspect renders with render_preview before committing "
    "to a final render_sprites, and poll get_job (with a short delay between polls, "
    "never a tight loop) until a job reaches a terminal status. "
    "When someone wants an installable pixel-agents furniture package, call "
    "render_sprites with pixel_agents set (asset_id, name, ...). Once that job's "
    "get_job status is 'succeeded', call deliver_pixel_agents_assets with its job_id -- "
    "this attaches the resulting pixel-agents.zip and preview.png directly to the "
    "reply. Never describe a download_url in your own words instead: it is an "
    "internal address a Discord user cannot reach, so text alone is not a usable "
    "answer for that case. "
    "deliver_pixel_agents_assets is NOT your final step -- it only stages the "
    "attachments. You must always send a separate final plain-text reply afterward "
    '(e.g. "Done! Attached the pixel-agents package."), even though the files were '
    "already attached by the tool call. Stopping with no text at all after a tool "
    "call is always wrong and is treated as a failure to answer. "
    "For requests that don't produce a pixel-agents package, use the tools you're "
    "given as needed, then reply with your final answer as plain text; that text is "
    "sent back directly, so make it complete and self-contained."
)
# Off by default -- verbose per-tool-call logging (tool name, arguments,
# and result/error for every call the LLM makes) is noisy in normal
# operation and only useful while actively diagnosing a tool-calling
# issue. Same convention as architect's/painter's own `debug_logging`.
DEFAULT_DEBUG_LOGGING = False
# `None` means "use corridor's own shared default" (see
# `corridor/infrastructure/llm_client.py`), not "no timeout" -- see
# `GlobalSettings.request_timeout_seconds`'s own docstring for why animator
# (unlike architect/painter) has a real per-agent override at all.
DEFAULT_REQUEST_TIMEOUT_SECONDS: float | None = None

GLOBAL_DEFAULTS: dict[str, object] = {
    "max_tool_calls": DEFAULT_MAX_TOOL_CALLS,
    "system_prompt": DEFAULT_SYSTEM_PROMPT,
    "debug_logging": DEFAULT_DEBUG_LOGGING,
    "request_timeout_seconds": DEFAULT_REQUEST_TIMEOUT_SECONDS,
}


class RedAnimatorRepository:
    """The typed boundary around this cog's Red Config storage."""

    def __init__(self, config: Any) -> None:
        self._config = config

    @classmethod
    def create(cls, cog: object) -> RedAnimatorRepository:
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
            request_timeout_seconds=cast(
                "float | None", await self._config.request_timeout_seconds()
            ),
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

    async def set_request_timeout(self, value: float | None) -> None:
        """`None` resets to corridor's own shared default -- see
        `GlobalSettings.request_timeout_seconds`'s own docstring."""

        if value is not None and (isinstance(value, bool) or value <= 0):
            raise ValueError(
                "Request timeout must be a positive number of seconds, or omitted for the default."
            )
        await self._config.request_timeout_seconds.set(value)


__all__ = [
    "CONFIG_IDENTIFIER",
    "DEFAULT_DEBUG_LOGGING",
    "DEFAULT_MAX_TOOL_CALLS",
    "DEFAULT_REQUEST_TIMEOUT_SECONDS",
    "DEFAULT_SYSTEM_PROMPT",
    "GLOBAL_DEFAULTS",
    "RedAnimatorRepository",
]
