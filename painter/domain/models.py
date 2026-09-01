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
    agent -- see docs/agent-directory-design.md. Browser transport belongs to
    CCTV, and the shared editor aggregate is reached through Pixelagents -- see
    docs/cctv-design.md."""

    max_tool_calls: int
    system_prompt: str
    debug_logging: bool
