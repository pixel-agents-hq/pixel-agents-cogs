"""Pure business models. Zero framework imports -- this module never imports
discord.py, redbot, mcp, or corridor, so it is trivially unit-testable
without any of them installed."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ThirdPartyMcpServer:
    """One bot-owner-registered external MCP server. `base_url` is that
    server's own Streamable HTTP endpoint (e.g.
    `"http://freecad-mcp:8765/mcp"`) -- telephonepole never rewrites it,
    same as corridor's own `RegisteredMcpServer.base_url`
    (`corridor/domain/agent_tool_server.py`). `name` is telephonepole's own
    primary key, distinct from `base_url`: it's what a bot owner types in
    `[p]telephonepole remove <name>`/`[p]telephonepole agents <name>`, and
    lets the same URL be re-added under a new name after a rename without
    colliding with corridor's own base_url-keyed registry mid-swap.
    """

    name: str
    base_url: str


__all__ = ["ThirdPartyMcpServer"]
