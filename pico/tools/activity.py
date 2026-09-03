"""Shared plumbing for reporting Discord activity onto corridor's event bus.

Every pico tool that succeeds announces itself as `AgentReplied` on
corridor's bus, best-effort -- a bus failure must never turn an
already-completed action into a reported tool failure. `ReplyTool`,
`ConsultAgentTool`, and `CrossCogTool` each used to hand-roll the same
try/except+log.warning wrapper around that publish call; this collapses it
to one place. Each module's own `CorridorEvents` Protocol (typing its own
`self._corridor` attribute) stays separate on purpose -- see
`consult_agent_tool.py`'s docstring -- this only shares the publish
behavior, not that structural type.
"""

from __future__ import annotations

import logging
from typing import Protocol

from corridor.domain import AgentRef, AgentReplied

log = logging.getLogger("red.pico")


class CorridorEventPublisher(Protocol):
    async def publish_event(self, event: object) -> None: ...


async def publish_agent_replied(
    corridor: CorridorEventPublisher, agent: AgentRef, summary: str, *, tool_name: str
) -> None:
    try:
        await corridor.publish_event(AgentReplied(agent=agent, summary=summary))
    except Exception:
        log.warning("pico: %s could not publish an AgentReplied event", tool_name, exc_info=True)


__all__ = ["CorridorEventPublisher", "publish_agent_replied"]
