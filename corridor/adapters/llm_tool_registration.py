"""Scans a cog instance for `@corridor.adapters.llm_tool`-decorated commands
and turns each into a `RegisteredTool`, ready for `CogBase.register_tool`.

Duck-typed via each attribute's `.callback` rather than discord.py's
`Cog.walk_commands()`/`__cog_commands__` machinery -- confirmed (by reading
both) that a real discord.py `Command` *and* the test stub's `_FakeCommand`
(`corridor/testing.py`) both expose `.callback` as the exact, undecorated
function object `@llm_tool` marked, but the stub implements neither
`walk_commands()` nor `Cog.__new__`'s per-instance command copying --
relying on either would make this untestable under this repo's stub-based
suite. See docs/corridor-tool-registry-design.md.
"""

from __future__ import annotations

import inspect
from collections.abc import Mapping
from typing import Any

from ..domain import RegisteredTool
from .llm_tools import LLMToolSpec, llm_tool_spec


def collect_registered_tools(cog: object) -> list[RegisteredTool]:
    """Every `@llm_tool`-decorated command found on `cog`, as
    `RegisteredTool`s. Deduped by callback identity so a command reachable
    under more than one attribute name is only registered once."""

    found: list[RegisteredTool] = []
    seen: set[int] = set()
    for _name, attr in inspect.getmembers(cog):
        callback = getattr(attr, "callback", None)
        if callback is None:
            continue
        spec = llm_tool_spec(callback)
        if spec is None or id(callback) in seen:
            continue
        seen.add(id(callback))
        found.append(_build_registered_tool(cog, callback, spec))
    return found


def _build_registered_tool(cog: object, callback: Any, spec: LLMToolSpec) -> RegisteredTool:
    # `callback(cog, ctx, **raw_args)` -- calling the plain callback
    # directly with an explicit `cog`, not `command(ctx, ...)`: real
    # discord.py's `Command.__call__` auto-binds `self.cog`, but the test
    # stub's `_FakeCommand.__call__` does not (confirmed by reading both).
    # Calling `.callback` directly is the one invocation shape that behaves
    # identically in both environments -- it's also exactly what this
    # repo's own tests already do by hand
    # (`cog.time_command.callback(cog, ctx, ...)`).
    async def handler(ctx: object, raw_args: Mapping[str, object]) -> Mapping[str, object]:
        await callback(cog, ctx, **raw_args)
        return {"status": "ok"}

    return RegisteredTool(
        name=spec.name,
        description=spec.description,
        parameters=spec.parameters,
        handler=handler,
        required_group=spec.required_group,
    )


__all__ = ["collect_registered_tools"]
