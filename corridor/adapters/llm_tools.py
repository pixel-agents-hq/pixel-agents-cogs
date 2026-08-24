"""`@llm_tool`: mark a Red command callback as a cross-cog LLM tool.

Lives here, not in `corridor/domain/` -- unlike every other type in
`corridor.domain`, this module has a real, load-bearing dependency on
discord.py's own `Parameter`/`Signature` machinery (`discord.ext.commands`).
It needs that machinery to make a genuinely natural
`typing.Annotated[X, "a description"]` on a command parameter safe to use
*at all*: discord.py's own command-parameter resolution reads the exact
same annotation this decorator does, and (verified against the installed
`discord.py==2.7.1`) already gives `Annotated[X, Y]` a meaning of its own
-- `Y` is the actual converter/type to use, not descriptive metadata about
`X`. Left alone, a plain description string in `Y` makes discord.py try to
`eval()` it as Python source at cog load (`SyntaxError`); a custom
non-string sentinel in `Y` instead gets treated as the parameter's real
converter and breaks *every* invocation of the command with `BadArgument`
(both confirmed by driving discord.py's own `evaluate_annotation`/
`run_converters` directly -- see docs/corridor-tool-registry-design.md).

The fix: `@llm_tool` reads the natural, single-metadata-item `Annotated`
form for its own purposes, then -- before returning -- patches the
callback's `__signature__` to a version with `Annotated` stripped back to
the bare type, built from discord.py's own `Parameter` class. This isn't a
discord.py-specific trick: `inspect.Signature.from_callable()` has always
honored an explicit `__signature__` attribute over introspecting a
callable's raw code (confirmed directly against CPython's `inspect`
module), and discord.py's command construction goes through exactly that
call. discord.py's later parameter resolution (`Command.__init__` at
decoration time, `Command.transform()` at real invocation time) then never
sees `Annotated` at all -- it sees a completely ordinary, un-annotated-past-
the-base-type parameter, indistinguishable from one that was never
decorated with `@llm_tool` in the first place.
"""

from __future__ import annotations

import inspect
import types
import typing
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, TypeVar

from discord.ext.commands.parameters import Signature

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


def llm_tool(*, name: str, description: str, required_group: str | None = None) -> Callable[[F], F]:
    """Mark a Red command callback as a cross-cog, LLM-callable tool.

    Apply directly to the command's own callback -- the innermost
    decorator, right above `async def ...` -- so corridor's
    `CogBase.register_llm_tools()` (called from the owning cog's own
    `cog_load`) can find it and register it into the cross-cog tool
    registry automatically.

    `parameters`'s JSON Schema is inferred from the callback's own
    signature/type hints, skipping the leading `self`/`ctx` parameters
    every Red command has. Only `str`/`int`/`float`/`bool` (optionally
    `| None`) base types are supported -- anything else raises `TypeError`
    here, at decoration/import time, not later.

    Give a parameter a description an LLM would need by wrapping its type
    in `typing.Annotated`, exactly the way FastAPI/pydantic do:

    ```python
    timezone: Annotated[str | None, "An IANA time zone name, e.g. 'America/New_York'."] = None
    ```

    This is safe to write directly on a real Discord command parameter --
    `@llm_tool` strips `Annotated` back down to the bare type on the
    callback's exposed signature before discord.py's own command
    construction ever inspects it (see this module's docstring for why
    that step exists and what it protects against). Nothing needs to be
    duplicated: the type is written once, in its natural position.

    Bypasses whatever `@commands.check`-style decorators (`guild_only`,
    `is_owner`, ...) the command may also carry: corridor invokes the
    callback directly, not through discord.py's command dispatch, so any
    access control this tool needs must be enforced in the callback's own
    body -- `required_group` above, or an explicit `require_permission`
    call as the first statement, same as deskutils' `time_command`.
    """

    def decorator(func: F) -> F:
        hints = typing.get_type_hints(func, include_extras=True)
        original_params = list(Signature.from_callable(func).parameters.values())
        leading, tail = (
            original_params[:_LEADING_PARAMS_TO_SKIP],
            original_params[_LEADING_PARAMS_TO_SKIP:],
        )

        properties: dict[str, object] = {}
        required: list[str] = []
        clean_params = list(leading)
        for param in tail:
            annotation = hints.get(param.name, str)
            bare_type, param_description = _strip_annotated(annotation)
            json_type = _json_type_for(func, param.name, bare_type)
            prop: dict[str, object] = {"type": json_type}
            if param_description is not None:
                prop["description"] = param_description
            properties[param.name] = prop
            if param.default is inspect.Parameter.empty:
                required.append(param.name)
            clean_params.append(param.replace(annotation=bare_type))

        # The whole point: discord.py's own command construction (which
        # runs next, when the outer `@x.command(...)` decorator wraps this
        # same function) reads this signature, not the original one with
        # `Annotated` still in it.
        func.__signature__ = Signature(clean_params)  # type: ignore[attr-defined]

        spec = LLMToolSpec(
            name=name,
            description=description,
            parameters={"type": "object", "properties": properties, "required": required},
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


def _strip_annotated(annotation: object) -> tuple[object, str | None]:
    """`Annotated[X, "a description", ...]` -> `(X, "a description")`; any
    non-`Annotated` annotation passes through unchanged with no
    description. Only the first `str` metadata item counts as the
    description -- a non-string metadata item (a real discord.py converter,
    say) is left for discord.py itself to make sense of, not consumed
    here."""

    if typing.get_origin(annotation) is typing.Annotated:
        args = typing.get_args(annotation)
        description = next((meta for meta in args[1:] if isinstance(meta, str)), None)
        return args[0], description
    return annotation, None


def _json_type_for(func: Callable[..., object], param_name: str, annotation: object) -> str:
    if typing.get_origin(annotation) is types.UnionType:
        args = [arg for arg in typing.get_args(annotation) if arg is not type(None)]
        if len(args) == 1:
            return _json_type_for(func, param_name, args[0])
    json_type = _JSON_TYPES.get(annotation) if isinstance(annotation, type) else None
    if json_type is None:
        raise TypeError(
            f"llm_tool: {func.__qualname__}'s parameter {param_name!r} has an unsupported "
            f"type {annotation!r} -- only str/int/float/bool (optionally `| None`, optionally "
            'wrapped in Annotated[..., "a description"]) are inferable into a JSON Schema'
        )
    return json_type


__all__ = ["LLMToolSpec", "llm_tool", "llm_tool_spec"]
