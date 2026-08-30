"""Pure business models. Zero framework imports -- this module never imports
discord.py, redbot, a2a, or pydantic, so it is trivially unit-testable
without any of them installed."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class GlobalSettings:
    """Bot-owner-scope settings for painter's own tool-calling loop. The
    LLM *connection* lives in corridor (shared with pico and architect) --
    see docs/architect-design.md and `corridor.domain.LLMSettings`. The
    A2A *listener* also lives in corridor, shared by every registered
    agent -- see docs/agent-directory-design.md. Unlike architect, painter
    owns no `ws_host`/`ws_port` at all: it serves no WebSocket transport
    or Dashboard page of its own, and its office layout isn't its own
    private store either -- see docs/painter-design.md part A."""

    max_tool_calls: int
    system_prompt: str
    debug_logging: bool
