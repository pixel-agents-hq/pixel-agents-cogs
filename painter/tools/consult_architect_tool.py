"""ConsultArchitectTool: painter's one structural-read tool, letting its
own LLM ask architect what tiles/walls/furniture exist and where
(docs/painter-design.md §3/§7.2).

Unlike pico's `ConsultAgentTool` (one instance built per currently
registered agent, each turn), painter only ever consults one specific,
known agent -- architect -- so this tool resolves architect's current
A2A URL from `corridor.list_agents()` itself on every call, rather than
being handed a fixed `base_url` at construction time the way pico's tool
is. This also means painter degrades gracefully (a normal tool error, not
a crash) if architect is ever unloaded/unregistered -- consistent with
"networked, not coded" (docs/painter-design.md §2)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Protocol

from pydantic import BaseModel, Field

from ..infrastructure.architect_client import AgentAskResult, ArchitectRequestError


class ArchitectAsker(Protocol):
    """The slice of ArchitectClient this tool depends on -- see
    `pico/tools/consult_agent_tool.py`'s identical Protocol."""

    async def ask(
        self,
        *,
        base_url: str,
        text: str,
        on_activity: Callable[[str], Awaitable[None]] | None = None,
    ) -> AgentAskResult: ...


class SupportsListAgents(Protocol):
    def list_agents(self) -> object: ...


class ConsultArchitectInput(BaseModel):
    prompt: str = Field(
        description=(
            "The structural question or task to delegate to architect -- e.g. 'what furniture "
            "is in the room bounded by columns 5-10 and rows 2-6' or 'what are the office's "
            "dimensions'. Architect can report exact tile/furniture color too, but "
            "describe_tile_colors/describe_furniture_colors give you faster, more direct access "
            "to it than relaying a question through architect."
        )
    )


class ConsultArchitectOutput(BaseModel):
    status: str = "ok"
    answer: str | None = None
    error: str | None = None


class ConsultArchitectTool:
    name = "consult_architect"
    description = (
        "Ask architect what tiles/walls/furniture exist in the shared office layout and where. "
        "Each call is independent; architect has no memory of past consultations. For color, "
        "prefer describe_tile_colors/describe_furniture_colors directly -- faster and more "
        "precise than asking architect to relay it."
    )

    def __init__(self, client: ArchitectAsker, corridor: object) -> None:
        self._client = client
        self._corridor = corridor

    @property
    def Input(self) -> type[BaseModel]:
        return ConsultArchitectInput

    @property
    def Output(self) -> type[BaseModel]:
        return ConsultArchitectOutput

    async def handler(self, raw_input: BaseModel) -> BaseModel:
        assert isinstance(raw_input, ConsultArchitectInput)
        agent = next(
            (
                candidate
                for candidate in self._corridor.list_agents()  # type: ignore[attr-defined]
                if candidate.agent_key == "architect"
            ),
            None,
        )
        if agent is None:
            return ConsultArchitectOutput(
                status="error",
                error="architect is not currently registered with corridor's agent directory",
            )
        try:
            result = await self._client.ask(
                base_url=agent.card.supported_interfaces[0].url, text=raw_input.prompt
            )
        except ArchitectRequestError as exc:
            return ConsultArchitectOutput(status="error", error=str(exc))
        return ConsultArchitectOutput(answer=result.answer)


__all__ = [
    "ArchitectAsker",
    "ConsultArchitectInput",
    "ConsultArchitectOutput",
    "ConsultArchitectTool",
]
