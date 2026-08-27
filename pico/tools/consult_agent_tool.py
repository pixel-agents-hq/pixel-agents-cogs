"""Delegates a sub-task to one currently-registered A2A agent. Replaces the
former hardcoded `ArchitectTool`/`[p]pico architecturl` pair -- pico builds
one `ConsultAgentTool` per entry in `corridor.list_agents()` each turn
instead (`adapters/listener.py`'s `_agent_tools`), so a new agent (architect,
or any future one) becomes available with zero pico-side code changes. See
docs/agent-directory-design.md.

The only tool in this cog that reaches outside corridor's cross-cog registry
and outside Discord entirely -- if the target agent is unloaded or
unreachable at call time, that's reported back to the LLM as a tool error
rather than surfaced to the Discord user directly -- pico still only ever
replies through `ReplyTool`.
"""

from __future__ import annotations

import logging
from typing import Protocol

from pydantic import BaseModel, Field

from ..infrastructure.architect_client import ArchitectRequestError

log = logging.getLogger("red.pico")


class ConsultAgentInput(BaseModel):
    prompt: str = Field(description="The task or question to delegate to this agent.")


class ConsultAgentOutput(BaseModel):
    status: str
    answer: str | None = None
    error: str | None = None


class ArchitectAsker(Protocol):
    """The slice of ArchitectClient this tool depends on -- name kept from
    architect's original hardcoded tool; the client itself is generic
    (`ask(base_url=..., text=...)`), not architect-specific."""

    async def ask(self, *, base_url: str, text: str) -> str: ...


class ConsultAgentTool:
    """One instance per currently-registered agent, built fresh each turn
    by `_agent_tools` from corridor's `AgentDirectoryService.list_agents()`
    entries. `name`/`description` come from that agent's own `AgentCard` --
    `card.description` is written by the registering agent
    (`architect/infrastructure/a2a_server.py`'s `AGENT_DESCRIPTION`, or any
    future agent's own), not hardcoded here."""

    def __init__(
        self, client: ArchitectAsker, *, agent_key: str, base_url: str, description: str
    ) -> None:
        self.name = f"consult_{agent_key}"
        self.description = description or f"Delegate a task to {agent_key}."
        self._client = client
        self._base_url = base_url

    @property
    def Input(self) -> type[BaseModel]:
        return ConsultAgentInput

    @property
    def Output(self) -> type[BaseModel]:
        return ConsultAgentOutput

    async def handler(self, raw_input: BaseModel) -> BaseModel:
        assert isinstance(raw_input, ConsultAgentInput)
        try:
            answer = await self._client.ask(base_url=self._base_url, text=raw_input.prompt)
        except ArchitectRequestError as exc:
            log.warning("pico: %s failed: %s", self.name, exc)
            return ConsultAgentOutput(status="error", error=str(exc))
        return ConsultAgentOutput(status="ok", answer=answer)


__all__ = ["ArchitectAsker", "ConsultAgentInput", "ConsultAgentOutput", "ConsultAgentTool"]
