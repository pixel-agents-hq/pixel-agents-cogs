"""Delegates a sub-task to architect over A2A. The only tool in this cog
that reaches outside corridor's cross-cog registry and outside Discord
entirely -- see docs/architect-design.md section 4's sequence diagram.

Only offered to the LLM once an owner has set `[p]pico architect url`
(`adapters/listener.py` omits this tool otherwise); if architect is
unloaded or unreachable at call time, that's reported back to the LLM as a
tool error rather than surfaced to the Discord user directly -- pico still
only ever replies through `ReplyTool`.
"""

from __future__ import annotations

import logging
from typing import Protocol

from pydantic import BaseModel, Field

from ..infrastructure.architect_client import ArchitectRequestError

log = logging.getLogger("red.pico")


class ConsultArchitectInput(BaseModel):
    prompt: str = Field(description="The task or question to delegate to architect.")


class ConsultArchitectOutput(BaseModel):
    status: str
    answer: str | None = None
    error: str | None = None


class ArchitectAsker(Protocol):
    """The slice of ArchitectClient this tool depends on."""

    async def ask(self, *, base_url: str, text: str) -> str: ...


class ArchitectTool:
    name = "consult_architect"
    description = (
        "Delegate a task or question to architect, a separate LLM agent reachable "
        "over A2A, and return its answer."
    )

    def __init__(self, client: ArchitectAsker, *, base_url: str) -> None:
        self._client = client
        self._base_url = base_url

    @property
    def Input(self) -> type[BaseModel]:
        return ConsultArchitectInput

    @property
    def Output(self) -> type[BaseModel]:
        return ConsultArchitectOutput

    async def handler(self, raw_input: BaseModel) -> BaseModel:
        assert isinstance(raw_input, ConsultArchitectInput)
        try:
            answer = await self._client.ask(base_url=self._base_url, text=raw_input.prompt)
        except ArchitectRequestError as exc:
            log.warning("pico: consult_architect failed: %s", exc)
            return ConsultArchitectOutput(status="error", error=str(exc))
        return ConsultArchitectOutput(status="ok", answer=answer)


__all__ = ["ArchitectAsker", "ArchitectTool", "ConsultArchitectInput", "ConsultArchitectOutput"]
