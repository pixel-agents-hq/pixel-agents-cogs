"""Domain model for corridor's `AgentToolServerRegistry`
(`corridor/application/agent_tool_server_registry.py`) -- a cog-owned MCP
tools server that a registered A2A agent's own tool-calling loop may call
through corridor, without either cog importing the other. See
docs/suggestionbox-design.md.

Zero framework imports, like every other module in this package except
`agent_directory.py`'s own deliberate exception -- `mcp` (the wire client
that actually talks to a registered server) lives entirely in
`corridor/infrastructure/mcp_client.py`, never here.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

# agent_key -> may this agent use the registering server's tools. Supplied
# by the registering cog itself at registration time -- deliberately not a
# separate filter-registration step the way ToolRegistryService's
# ToolVisibilityFilter is (see docs/suggestionbox-design.md §2): there is
# exactly one owner per registered server, deciding which agents may use
# its *own* tools, not a third party opining on someone else's.
AgentAllowedCheck = Callable[[str], Awaitable[bool]]


@dataclass(frozen=True, slots=True)
class RegisteredMcpServer:
    """One MCP tools server, registered into `AgentToolServerRegistry` by
    its owning cog's own `cog_load` -- the same in-process registration
    shape `RegisteredTool`/`RegisteredAgent` already use, generalized to
    "a whole MCP server another cog runs" instead of one in-process
    callable or one A2A executor.

    `base_url` is that server's own Streamable HTTP endpoint (e.g.
    `"http://127.0.0.1:8934/mcp"`) -- unlike `RegisteredAgent`'s card,
    corridor never rewrites this: the registering cog binds and owns this
    listener itself (see docs/suggestionbox-design.md §3 on why this
    isn't centralized onto corridor's own shared A2A listener), so it
    already knows its own reachable address.
    """

    owner: str
    base_url: str
    agent_allowed: AgentAllowedCheck


__all__ = ["AgentAllowedCheck", "RegisteredMcpServer"]
