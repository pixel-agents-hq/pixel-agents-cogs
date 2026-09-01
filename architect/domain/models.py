"""Pure business models. Zero framework imports -- this module never imports
discord.py, redbot, a2a, or pydantic, so it is trivially unit-testable
without any of them installed."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class GlobalSettings:
    """Bot-owner-scope settings for architect's own tool-calling loop. The
    LLM *connection* lives in corridor (shared with pico) -- see
    docs/architect-design.md and `corridor.domain.LLMSettings`. The A2A
    *listener* also lives in corridor now, shared by every registered
    agent -- see docs/agent-directory-design.md; architect no longer has
    its own `a2a_host`/`a2a_port` fields. architect no longer binds any
    WebSocket server or webview of its own either -- `cctv` is the only
    cog serving a dashboard page (docs/cctv-design.md), so there is no
    `ws_host`/`ws_port` here anymore."""

    max_tool_calls: int
    system_prompt: str
    debug_logging: bool
