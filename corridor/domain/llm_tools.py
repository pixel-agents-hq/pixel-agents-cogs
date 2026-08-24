"""`@llm_tool`: mark a Red command callback as a cross-cog LLM tool.

Framework-neutral -- only `inspect`/`typing` on the decorated callback's own
signature, no discord.py/redbot import -- so it's safe to apply at module
import time in any cog, before corridor is even guaranteed loaded. The
actual registration into corridor's cross-cog tool registry happens later,
at the registering cog's own `cog_load`, via
`corridor.adapters.llm_tool_registration.collect_registered_tools` (a
duck-typed scan of the cog for commands whose callback carries the marker
this module attaches) -- see docs/corridor-tool-registry-design.md.
"""

from __future__ import annotations

import inspect
import types
import typing
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, TypeVar

_MARKER_ATTR = "__corridor_llm_tool__"

F = TypeVar("F", bound=Callable[..., Any])

_JSON_TYPES: dict[type, str] = {str: "string", int: "integer", float: "number", bool: "boolean"}

# Every Red command callback starts `(self, ctx, ...)` -- neither is
# LLM-visible, so the schema is built from whatever comes after them.
_LEADING_PARAMS_TO_SKIP = 2


@dataclass(frozen=True, slots=True)
class LLMToolSpec:
    """Everything `collect_registered_tools` needs to build a
    `RegisteredTool` from a decorated callback, short of the live cog
    instance and Command object that only exist once the cog is loaded."""

    name: str
    description: str
    parameters: dict[str, object]
    required_group: str | None


def llm_tool(
    *,
    name: str,
    description: str,
    required_group: str | None = None,
    parameter_descriptions: Mapping[str, str] | None = None,
) -> Callable[[F], F]:
    """Mark a Red command callback as a cross-cog, LLM-callable tool.

    Apply directly to the command's own callback -- the innermost
    decorator, right above `async def ...` -- so corridor's
    `CogBase.register_llm_tools()` (called from the owning cog's own
    `cog_load`) can find it and register it into the cross-cog tool
    registry automatically.

    `parameters`'s JSON Schema is inferred from the callback's own
    signature/type hints, skipping the leading `self`/`ctx` parameters
    every Red command has. Only `str`/`int`/`float`/`bool` (optionally
    `| None`) parameter types are supported -- anything else raises
    `TypeError` here, at decoration/import time, not later.

    `parameter_descriptions` adds a per-parameter `"description"` to that
    schema, e.g. `parameter_descriptions={"timezone": "An IANA time zone "
    "name, e.g. 'America/New_York'."}` -- worth setting for any parameter
    whose name/type alone wouldn't tell an LLM what to pass. A key with no
    matching parameter raises `TypeError` here too, so a typo (or a stale
    description left behind after a rename) fails loudly instead of
    silently describing nothing.

    Do **not** reach for `typing.Annotated[X, "a description"]` on the
    parameter itself to do this instead -- it looks like the obvious move
    (it's how FastAPI/pydantic attach field metadata) but this repo's
    installed discord.py already claims `Annotated[X, Y]` for its own
    converter machinery (`discord.utils.evaluate_annotation` treats `Y` as
    the *actual* type/converter to use, not descriptive text) and will try
    to `eval()` a plain description string as Python source, raising
    `SyntaxError` at cog load. `parameter_descriptions` above is the safe
    way to attach a description without touching the annotation
    discord.py itself also reads.

    Bypasses whatever `@commands.check`-style decorators (`guild_only`,
    `is_owner`, ...) the command may also carry: corridor invokes the
    callback directly, not through discord.py's command dispatch, so any
    access control this tool needs must be enforced in the callback's own
    body -- `required_group` above, or an explicit `require_permission`
    call as the first statement, same as deskutils' `time_command`.
    """

    def decorator(func: F) -> F:
        spec = LLMToolSpec(
            name=name,
            description=description,
            parameters=_parameters_schema(func, parameter_descriptions or {}),
            required_group=required_group,
        )
        setattr(func, _MARKER_ATTR, spec)
        return func

    return decorator


def llm_tool_spec(func: Callable[..., object]) -> LLMToolSpec | None:
    """Read back the spec `@llm_tool` attached to `func`, if any.

    Used by corridor's own scanner, and equally usable by a decorated
    cog's own tests to assert its command really did get tagged correctly
    without needing corridor's adapter-layer scanning machinery.
    """

    return getattr(func, _MARKER_ATTR, None)


def _parameters_schema(
    func: Callable[..., object], parameter_descriptions: Mapping[str, str]
) -> dict[str, object]:
    hints = typing.get_type_hints(func)
    params = list(inspect.signature(func).parameters.values())[_LEADING_PARAMS_TO_SKIP:]
    param_names = {param.name for param in params}

    unknown = set(parameter_descriptions) - param_names
    if unknown:
        raise TypeError(
            f"llm_tool: {func.__qualname__}'s parameter_descriptions names unknown "
            f"parameter(s) {sorted(unknown)!r} -- must match a parameter on the callback"
        )

    properties: dict[str, object] = {}
    required: list[str] = []
    for param in params:
        annotation = hints.get(param.name, str)
        json_type = _json_type_for(func, param.name, annotation)
        prop: dict[str, object] = {"type": json_type}
        description = parameter_descriptions.get(param.name)
        if description is not None:
            prop["description"] = description
        properties[param.name] = prop
        if param.default is inspect.Parameter.empty:
            required.append(param.name)
    return {"type": "object", "properties": properties, "required": required}


def _json_type_for(func: Callable[..., object], param_name: str, annotation: object) -> str:
    if typing.get_origin(annotation) is types.UnionType:
        args = [arg for arg in typing.get_args(annotation) if arg is not type(None)]
        if len(args) == 1:
            return _json_type_for(func, param_name, args[0])
    json_type = _JSON_TYPES.get(annotation) if isinstance(annotation, type) else None
    if json_type is None:
        raise TypeError(
            f"llm_tool: {func.__qualname__}'s parameter {param_name!r} has an unsupported "
            f"type {annotation!r} -- only str/int/float/bool (optionally `| None`) are "
            "inferable into a JSON Schema"
        )
    return json_type


__all__ = ["LLMToolSpec", "llm_tool", "llm_tool_spec"]
