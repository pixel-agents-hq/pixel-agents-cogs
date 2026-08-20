"""The contract every Pico tool implements.

`tools/` is a deliberate deviation from the raw cookiecutter skeleton: a
top-level package sibling to the four cookiecutter layers, not tucked
inside `application/`. Tools are the extensible unit iteration 2 builds on
(decorator-based command->tool conversion, permission-gated availability),
worth their own package from the start rather than a folder promoted later.

Iteration 1 ships exactly one tool (`reply_tool.py`), hand-written against
this Protocol -- no registry or decorator machinery yet.
"""

from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel


class ToolSpec(Protocol):
    """`name`/`description` feed the LLM's tool listing; `Input`/`Output`
    are the Pydantic v2 models `ToolLoopService` validates a call's
    arguments and return value against. `handler` does the actual work and
    must not raise for expected failure modes -- report those through
    `Output` fields instead (see `reply_tool.py`)."""

    name: str
    description: str

    # Properties rather than plain attributes: mypy treats Protocol data
    # attributes invariantly, which would reject any tool whose Input/Output
    # are a concrete subclass of BaseModel (i.e. every real tool) as not
    # matching `type[BaseModel]`. A property's return type is checked
    # covariantly instead, so `Input = ReplyInput` on a concrete tool
    # satisfies this just fine.
    @property
    def Input(self) -> type[BaseModel]: ...

    @property
    def Output(self) -> type[BaseModel]: ...

    async def handler(self, raw_input: BaseModel) -> BaseModel: ...


__all__ = ["ToolSpec"]
