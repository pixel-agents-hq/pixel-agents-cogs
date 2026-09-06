"""Animator's A2A surface: agent card + executor.

Animator owns no A2A listener of its own (there is no such listener in
this repo for any agent): the `AgentCard`/`AgentExecutor` built here are
handed to `corridor.register_agent(...)` at `cog_load`, and corridor mounts
them on its own shared listener alongside every other registered agent.
`AgentCard.supported_interfaces[0].url` set here is a placeholder --
corridor overwrites it with its own configured host/port + this agent's
mount path before storing it.

The `AgentExecutor` scaffolding itself (`execute`/`_run_turn`/
`_fail_safely`/`cancel`) is shared with architect's/painter's identical
shape -- see `corridor/domain/agent_executor.py`'s own module docstring,
including its handling of an optional `attachments` field on the tool
loop's result (present here, absent for architect/painter)."""

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

log = logging.getLogger("red.animator")

AGENT_NAME = "animator"
AGENT_VERSION = "0.1.0"
AGENT_DESCRIPTION = (
    "An independent LLM agent reachable only over A2A -- never Discord-user-facing. "
    "Consult it to model and render pixel-art sprites and animations: describe an "
    "object or a change, optionally referencing an uploaded image, and it drives "
    "pixel-art-mcp's Blender-backed tools (creating/editing a 3D scene, previewing "
    "renders, exporting directional or animated sprite sheets). It has no memory of "
    "past consultations -- each prompt is answered on its own, so restate any "
    "earlier project ID or context a follow-up needs. "
    "When asked for an installable pixel-agents furniture package, it delivers the "
    "resulting pixel-agents.zip and preview.png as real Discord attachments, not "
    "just a description of them."
)


def build_agent_card(*, tools: Sequence[ToolSpec]) -> AgentCard:
    """One skill per tool animator currently offers (its own
    `deliver_pixel_agents_assets` plus whatever pixel-art-mcp tools
    `[p]telephonepole` currently has enabled for it). The URL is a
    placeholder (`corridor.register_agent` overwrites it)."""

    return _build_agent_card(
        name=AGENT_NAME,
        description=AGENT_DESCRIPTION,
        version=AGENT_VERSION,
        tools=tools,
        tag=AGENT_NAME,
    )


class AnimatorAgentExecutor(GenericAgentExecutor):
    """Bridges one inbound A2A message to animator's own bounded
    `ToolLoopService`, using the same corridor-shared LLM connection
    pico/architect/painter use -- fixes `agent_name`/`logger` on
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
            agent_name="Animator",
            logger=log,
            tool_loop=tool_loop,
            tools=tools,
            settings=settings,
            llm_settings=llm_settings,
            publish_activity=publish_activity,
            mcp_tools=mcp_tools,
        )


__all__ = ["AnimatorAgentExecutor", "build_agent_card"]
