"""The contract every architect tool implements.

A deliberate parallel copy of `pico/tools/base.py`'s `ToolSpec` Protocol,
not a shared import -- pico and architect are independent agents with
independent tool sets; see docs/architect-design.md on why this is
duplicated rather than factored into a shared package."""

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

    # Properties rather than plain attributes: mypy treats Protocol data
    # attributes invariantly, which would reject any tool whose Input/Output
    # are a concrete subclass of BaseModel (i.e. every real tool) as not
    # matching `type[BaseModel]`. A property's return type is checked
    # covariantly instead, so `Input = SomeInput` on a concrete tool
    # satisfies this just fine.
    @property
    def Input(self) -> type[BaseModel]: ...

    @property
    def Output(self) -> type[BaseModel]: ...

    async def handler(self, raw_input: BaseModel) -> BaseModel: ...


__all__ = ["ToolSpec"]
