"""Painter's A2A surface: agent card + executor. See
docs/agent-directory-design.md.

Painter no longer owns an A2A listener of its own (there is no such
listener in this repo for any agent -- see docs/agent-directory-design.md):
the `AgentCard`/`AgentExecutor` built here are handed to
`corridor.register_agent(...)` at `cog_load`, and corridor mounts them on
its own shared listener alongside every other registered agent.
`AgentCard.supported_interfaces[0].url` set here is a placeholder --
corridor overwrites it with its own configured host/port + this agent's
mount path before storing it.

The `AgentExecutor` scaffolding itself (`execute`/`_run_turn`/
`_fail_safely`/`cancel`) is shared with architect's identical shape --
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

log = logging.getLogger("red.painter")

AGENT_NAME = "painter"
AGENT_VERSION = "0.1.0"
AGENT_DESCRIPTION = (
    "A second, independent LLM agent reachable only over A2A -- never "
    "Discord-user-facing. Consult it to delegate a color-related sub-task: "
    "recoloring floor tiles, walls, or furniture, or reporting the office's "
    "current colors. It only acts on what the delegated prompt states as "
    "an explicit instruction. It has no memory of past consultations -- "
    "each prompt is answered on its own, so restate any earlier context a "
    "follow-up needs."
    "\n\nIt shares one persistent office layout with Architect: Architect "
    "knows what tiles, walls, and furniture exist and where, and can "
    "report their exact color too, but can never change one. Painter is "
    "the color specialist and can read/change color, but can never add, "
    "remove, move, or otherwise restructure anything -- forward structural "
    "requests (adding furniture, resizing zones, etc.) to Architect "
    "instead, not to Painter."
)


def build_agent_card(*, tools: Sequence[ToolSpec]) -> AgentCard:
    """One skill per tool painter currently offers. The URL is a
    placeholder (`corridor.register_agent` overwrites it)."""

    return _build_agent_card(
        name=AGENT_NAME,
        description=AGENT_DESCRIPTION,
        version=AGENT_VERSION,
        tools=tools,
        tag=AGENT_NAME,
    )


class PainterAgentExecutor(GenericAgentExecutor):
    """Bridges one inbound A2A message to painter's own bounded
    `ToolLoopService`, using the same corridor-shared LLM connection pico
    and architect use -- fixes `agent_name`/`logger` on
    `GenericAgentExecutor`, see that class's own docstring for the shared
    mechanics."""

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
            agent_name="Painter",
            logger=log,
            tool_loop=tool_loop,
            tools=tools,
            settings=settings,
            llm_settings=llm_settings,
            publish_activity=publish_activity,
            mcp_tools=mcp_tools,
        )


__all__ = ["PainterAgentExecutor", "build_agent_card"]
