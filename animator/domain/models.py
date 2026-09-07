"""Pure business models. Zero framework imports -- this module never imports
discord.py, redbot, a2a, or pydantic, so it is trivially unit-testable
without any of them installed."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class GlobalSettings:
    """Bot-owner-scope settings for animator's own tool-calling loop. The
    LLM *connection* lives in corridor (shared with pico/architect/painter)
    -- see `corridor.domain.LLMSettings`. The A2A *listener* also lives in
    corridor, shared by every registered agent.

    `request_timeout_seconds` overrides corridor's shared LLM connection's
    own default total-request timeout (see `corridor/infrastructure/
    llm_client.py`) for animator's calls specifically -- a real production
    timeout was hit modeling/rendering via pixel-art-mcp, whose tool calls
    (Blender scripting, rendering) can run far longer than a typical chat
    completion. `None` (the default) means "use corridor's own default,"
    not "no timeout." Set via `[p]animator requesttimeout`
    (`infrastructure/settings_repository.py`). This dataclass keeps
    structurally satisfying corridor's shared `SupportsAgentSettings`
    protocol (`corridor/domain/agent_executor.py`), the same one
    bootcamp's own per-agent-configurable `CustomAgent` implements.

    `read_timeout_seconds` is a separate, independent timeout: it bounds
    one MCP tool call's own wait for a response (`corridor.infrastructure.
    mcp_client.McpClientPool.call_tool`'s `timeout_seconds`), not the LLM
    completion call `request_timeout_seconds` covers. Pixel-art-mcp's
    `wait_for_job` tool blocks server-side for up to its own configured
    maximum -- this needs to be at least that long, or every wait_for_job
    call fails before the server ever gets a chance to respond. `None`
    (the default) means "use McpClientPool's own default" (wait
    indefinitely), not "no timeout." Set via `[p]animator readtimeout`."""

    max_tool_calls: int
    system_prompt: str
    debug_logging: bool
    request_timeout_seconds: float | None = None
    read_timeout_seconds: float | None = None
