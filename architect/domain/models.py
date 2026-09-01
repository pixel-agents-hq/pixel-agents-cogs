"""Pure business models. Zero framework imports -- this module never imports
discord.py, redbot, a2a, or pydantic, so it is trivially unit-testable
without any of them installed."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class GlobalSettings:
    """Bot-owner settings for Architect's tool-calling loop."""

    max_tool_calls: int
    system_prompt: str
    debug_logging: bool
