"""The contract every painter tool implements.

A deliberate parallel copy of `architect/tools/base.py`'s `ToolSpec`
Protocol (itself a parallel copy of pico's own), not a shared import --
each agent's tool set is independent; see docs/architect-design.md on why
this is duplicated rather than factored into a shared package."""

from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel


class ToolSpec(Protocol):
    """`name`/`description` feed the LLM's tool listing; `Input`/`Output`
    are the Pydantic v2 models `ToolLoopService` validates a call's
    arguments and return value against. `handler` does the actual work and
    must not raise for expected failure modes -- report those through
    `Output` fields instead."""

    name: str
    description: str

    @property
    def Input(self) -> type[BaseModel]: ...

    @property
    def Output(self) -> type[BaseModel]: ...

    async def handler(self, raw_input: BaseModel) -> BaseModel: ...


__all__ = ["ToolSpec"]
