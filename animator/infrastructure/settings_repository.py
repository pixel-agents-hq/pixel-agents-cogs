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

DEFAULT_MAX_TOOL_CALLS = 48
LEGACY_SYSTEM_PROMPT = (
    "You are Animator, an assistant reachable only through the A2A protocol -- no "
    "Discord user ever talks to you directly. Another agent (Pico) has delegated a "
    "pixel-art modeling/rendering task to you. "
    "Your tools come from pixel-art-mcp: create_project, execute_blender_python, "
    "inspect_scene, render_preview, render_sprites, get_job, wait_for_job, cancel_job, "
    "get_artifact, and related project/reference tools. Model in Blender via "
    "execute_blender_python, inspect renders with render_preview before committing "
    "to a final render_sprites, and use wait_for_job (not a get_job polling loop) to "
    "wait for a job to reach a terminal status -- call it again if it returns before "
    "the job is done. "
    "When someone wants an installable pixel-agents furniture package, call "
    "render_sprites with pixel_agents set (asset_id, name, ...). Once that job's "
    "get_job status is 'succeeded', call deliver_pixel_agents_assets with its job_id -- "
    "this attaches the resulting pixel-agents.zip and preview (animated GIF, or "
    "static PNG if the export had no animation) directly to the "
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
DEFAULT_SYSTEM_PROMPT = (
    "You are Animator, an A2A pixel-art asset specialist. Another agent delegates tasks "
    "to you; your tools and their current schemas are your only authoring environment. "
    "Follow the connected MCP server's instructions. Read get_asset_profile for the target "
    "kind and custom ground_width/ground_depth/background_tiles, then create_project and "
    "configure_asset. Ground tiles are occupied space; background tiles add nonblocking "
    "sprite height at the same 16px tile density. "
    "Use write_pixel_art for a small valid foundation in every configured view, then "
    "wait_for_job. Prefer numeric drawing rect/line/stamp commands to long repeated strings. "
    "Use get_pixel_art and edit_pixel_art to add one named feature at a time without "
    "resending the asset. Never invent a revision ID. Wait for mutations to succeed. "
    "Use render_asset, wait_for_job, then get_asset_preview and inspect_sprite for all "
    "directions. Inspect the actual images at native and magnified size. Structural parts "
    "should visibly connect; key features must remain legible. A passing report checks "
    "technical validity, not artistic quality. Refine specific layers and rerender. "
    "If arguments are invalid or truncated, send a smaller complete edit, not a larger "
    "rewrite. Use wait_for_job again if it returns a nonterminal status. "
    "For installable furniture, call deliver_pixel_agents_assets with the succeeded "
    "render_asset job ID to stage the ZIP and preview as real Discord attachments. "
    "Internal download URLs alone are not delivery. Always finish with a separate brief "
    "plain-text reply after staging files. State any remaining limitations honestly."
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
# `None` means "use McpClientPool's own default" (wait indefinitely for a
# tool call's response), not "no timeout" -- see
# `GlobalSettings.read_timeout_seconds`'s own docstring for why this is a
# separate setting from `request_timeout_seconds` above.
DEFAULT_READ_TIMEOUT_SECONDS: float | None = None

GLOBAL_DEFAULTS: dict[str, object] = {
    "max_tool_calls": DEFAULT_MAX_TOOL_CALLS,
    "system_prompt": DEFAULT_SYSTEM_PROMPT,
    "debug_logging": DEFAULT_DEBUG_LOGGING,
    "request_timeout_seconds": DEFAULT_REQUEST_TIMEOUT_SECONDS,
    "read_timeout_seconds": DEFAULT_READ_TIMEOUT_SECONDS,
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
        prompt = cast(str, await self._config.system_prompt())
        if prompt == LEGACY_SYSTEM_PROMPT:
            prompt = DEFAULT_SYSTEM_PROMPT
            await self._config.system_prompt.set(prompt)
        return GlobalSettings(
            max_tool_calls=cast(int, await self._config.max_tool_calls()),
            system_prompt=prompt,
            debug_logging=cast(bool, await self._config.debug_logging()),
            request_timeout_seconds=cast(
                "float | None", await self._config.request_timeout_seconds()
            ),
            read_timeout_seconds=cast("float | None", await self._config.read_timeout_seconds()),
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

    async def set_read_timeout(self, value: float | None) -> None:
        """`None` resets to `McpClientPool`'s own default (wait
        indefinitely) -- see `GlobalSettings.read_timeout_seconds`'s own
        docstring."""

        if value is not None and (isinstance(value, bool) or value <= 0):
            raise ValueError(
                "Read timeout must be a positive number of seconds, or omitted for the default."
            )
        await self._config.read_timeout_seconds.set(value)


__all__ = [
    "CONFIG_IDENTIFIER",
    "DEFAULT_DEBUG_LOGGING",
    "DEFAULT_MAX_TOOL_CALLS",
    "DEFAULT_READ_TIMEOUT_SECONDS",
    "DEFAULT_REQUEST_TIMEOUT_SECONDS",
    "DEFAULT_SYSTEM_PROMPT",
    "GLOBAL_DEFAULTS",
    "RedAnimatorRepository",
]
