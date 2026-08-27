"""Pure business models. Zero framework imports -- this module never imports
discord.py, redbot, a2a, or pydantic, so it is trivially unit-testable
without any of them installed."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class GlobalSettings:
    """Bot-owner-scope settings for architect's own tool-calling loop, A2A
    listener, and office WebSocket server. The LLM *connection* lives in
    corridor (shared with pico) -- see docs/architect-design.md and
    `corridor.domain.LLMSettings`.

    `ws_host`/`ws_port` are a local bind address for architect's own office
    WebSocket server -- entirely separate from floorplan's own `ws_host`/
    `ws_port`, matching the "independent layout, independent live
    connection" requirement (see docs/architect-design.md). Reaching it
    from a browser at the public `/architect/ws` path
    (`infrastructure/websocket.py`'s `WEBSOCKET_PATH`) additionally needs a
    reverse-proxy rule an operator configures outside this repo, the same
    way floorplan's own `/ws` already does."""

    max_tool_calls: int
    system_prompt: str
    a2a_host: str
    a2a_port: int
    ws_host: str
    ws_port: int
    debug_logging: bool
