"""Turns a plain, undecorated Red command into a `RegisteredTool` for a
command the bot owner selected through toolbox's own UI (Phase 5) --
nobody wrote `@llm_tool()` on it.

Structurally mirrors corridor's own
`corridor/adapters/llm_tool_registration.py::collect_registered_tools` --
same duck-typed `inspect.getmembers(cog)` scan via each attribute's
`.callback`, same `handler`/`availability_check` shape -- deliberately, so
a dynamically-wrapped tool and an `@llm_tool`-decorated one are
indistinguishable once registered. The one difference: this scan is keyed
by toolbox's own `selected` set instead of a `@llm_tool` marker, and it
skips any command that *does* carry that marker outright -- the owning
cog's own `cog_load` already registers those; toolbox only fills the gap
for commands nobody decorated. See
docs/toolbox-command-tool-toggle-design.md.
"""

from __future__ import annotations

import inspect
from collections.abc import Mapping
from typing import Any

from corridor.domain import RegisteredTool, ToolAvailabilityCheck, infer_parameters
from corridor.domain.llm_tools import llm_tool_spec


def collect_wrappable_tools(cog: object, selected: frozenset[str]) -> list[RegisteredTool]:
    """Every command on `cog` whose qualified name is in `selected` and
    that has no `@llm_tool` spec of its own, as `RegisteredTool`s. Deduped
    by callback identity, same as corridor's own scanner."""

    found: list[RegisteredTool] = []
    seen: set[int] = set()
    for _name, attr in inspect.getmembers(cog):
        callback = getattr(attr, "callback", None)
        if callback is None:
            continue
        if llm_tool_spec(callback) is not None:
            continue
        qualified_name = getattr(attr, "qualified_name", None)
        if not isinstance(qualified_name, str) or qualified_name not in selected:
            continue
        if id(callback) in seen:
            continue
        seen.add(id(callback))
        found.append(_build_wrapped_tool(cog, attr, callback, qualified_name))
    return found


def _build_wrapped_tool(
    cog: object, command: object, callback: Any, qualified_name: str
) -> RegisteredTool:
    # Same direct-callback invocation shape as corridor's own scanner, for
    # the same reason: it behaves identically against real discord.py and
    # this repo's test stub alike.
    async def handler(ctx: object, raw_args: Mapping[str, object]) -> Mapping[str, object]:
        result = await callback(cog, ctx, **raw_args)
        if result is None:
            return {"status": "ok"}
        if not isinstance(result, Mapping):
            raise TypeError(
                f"toolbox: {callback.__qualname__} returned {type(result).__name__}, "
                "expected a mapping or None"
            )
        if not all(isinstance(key, str) for key in result):
            raise TypeError(
                f"toolbox: {callback.__qualname__} returned a mapping with a non-string key"
            )
        return dict(result)

    description = inspect.getdoc(callback) or f"Run the Discord command `{qualified_name}`."

    availability_check: ToolAvailabilityCheck | None = None
    can_run = getattr(command, "can_run", None)
    if callable(can_run):

        async def inferred_availability_check(ctx: object) -> bool:
            return bool(await can_run(ctx, check_all_parents=True))

        availability_check = inferred_availability_check

    return RegisteredTool(
        name="_".join(qualified_name.split()),
        description=description,
        parameters=infer_parameters(callback, strict=False),
        handler=handler,
        required_group=None,
        availability_check=availability_check,
    )


__all__ = ["collect_wrappable_tools"]
