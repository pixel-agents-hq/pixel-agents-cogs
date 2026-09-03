"""Architect's A2A surface: agent card + executor. See
docs/architect-design.md section 4 and docs/agent-directory-design.md.

Architect no longer owns an A2A listener of its own (see
docs/agent-directory-design.md): the `AgentCard`/`AgentExecutor` built here
are handed to `corridor.register_agent(...)` at `cog_load`, and corridor
mounts them on its own shared listener alongside every other registered
agent. `AgentCard.supported_interfaces[0].url` set here is a placeholder --
corridor overwrites it with its own configured host/port + this agent's
mount path before storing it (`corridor.domain.card_with_url`).

The `AgentExecutor` scaffolding itself (`execute`/`_run_turn`/
`_fail_safely`/`cancel`) is shared with painter's identical shape --
see `corridor/domain/agent_executor.py`'s own module docstring."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Sequence

from a2a.types import AgentCard

from corridor.domain import LLMSettings
from corridor.domain.agent_executor import (
    GenericAgentExecutor,
    SupportsAgentSettings,
    SupportsToolLoop,
)
from corridor.domain.agent_executor import build_agent_card as _build_agent_card

from ..tools.base import ToolSpec

log = logging.getLogger("red.architect")

AGENT_NAME = "architect"
AGENT_VERSION = "0.1.0"
AGENT_DESCRIPTION = (
    "A second, independent LLM agent reachable only over A2A -- never "
    "Discord-user-facing. Consult it to delegate a sub-task. It only acts on "
    "what the delegated prompt states as an explicit instruction -- a goal "
    "or rationale mentioned alongside that instruction is read as context, "
    "not as a second thing to also do, so list every step you want it to "
    "carry out. It has no memory of past consultations -- each prompt is "
    "answered on its own, so restate any earlier context a follow-up needs."
    "\n\nIt maintains one persistent office layout (zones, furniture, "
    "seats) and can already see and query that layout itself -- including "
    "resolving descriptive or spatial phrases like 'the chair in the "
    "lower right corner' into exact tiles/ids on its own. Forward such "
    "requests as an explicit instruction; do not ask the user for a "
    "screenshot, layout link, or extra positional detail on architect's "
    "behalf -- architect will ask back if it genuinely can't resolve "
    "something."
)


def build_agent_card(*, tools: Sequence[ToolSpec]) -> AgentCard:
    """One skill per tool architect currently offers. The URL is a
    placeholder (`corridor.register_agent` overwrites it) -- this card no
    longer describes a listener architect itself binds."""

    return _build_agent_card(
        name=AGENT_NAME,
        description=AGENT_DESCRIPTION,
        version=AGENT_VERSION,
        tools=tools,
        tag=AGENT_NAME,
    )


class ArchitectAgentExecutor(GenericAgentExecutor):
    """Bridges one inbound A2A message to architect's own bounded
    `ToolLoopService`, using the same corridor-shared LLM connection pico
    uses -- fixes `agent_name`/`logger` on `GenericAgentExecutor`, see that
    class's own docstring for the shared mechanics."""

    def __init__(
        self,
        *,
        tool_loop: SupportsToolLoop,
        tools: Sequence[ToolSpec],
        settings: Callable[[], Awaitable[SupportsAgentSettings]],
        llm_settings: Callable[[], Awaitable[LLMSettings]],
        publish_activity: Callable[[str], Awaitable[None]] | None = None,
        mcp_tools: Callable[[], Awaitable[Sequence[ToolSpec]]] | None = None,
    ) -> None:
        super().__init__(
            agent_name="Architect",
            logger=log,
            tool_loop=tool_loop,
            tools=tools,
            settings=settings,
            llm_settings=llm_settings,
            publish_activity=publish_activity,
            mcp_tools=mcp_tools,
        )


__all__ = ["ArchitectAgentExecutor", "build_agent_card"]
