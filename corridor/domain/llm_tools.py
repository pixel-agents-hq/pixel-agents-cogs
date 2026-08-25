"""`@llm_tool`: mark a Red command callback as a cross-cog LLM tool.

Framework-neutral -- only `inspect`/`typing` on the decorated callback's own
signature, no discord.py/redbot import -- so it's safe to apply at module
import time in any cog, before corridor is even guaranteed loaded. The
actual registration into corridor's cross-cog tool registry happens later,
at the registering cog's own `cog_load`, via
`corridor.adapters.llm_tool_registration.collect_registered_tools` (a
duck-typed scan of the cog for commands whose callback carries the marker
this module attaches) -- see docs/corridor-tool-registry-design.md.

Per-parameter JSON Schema metadata uses natural
`typing.Annotated[X, ToolDescription(...)]` syntax, similar to
FastAPI/pydantic. This is safe on a *real* Discord command parameter --
despite discord.py's own command-parameter resolution reading the exact
same annotation and (verified against `discord.py==2.7.1`) already giving
`Annotated[X, Y]` a meaning of its own (`Y` is the real type/converter to
use, not descriptive metadata) -- because `@llm_tool` mutates the
callback's own `func.__annotations__` in place, replacing each
`Annotated[...]`-wrapped parameter with its bare type, before returning.
Every future re-derivation of the signature discord.py ever does (at
decoration time, and again every time a `Cog` instance is built --
`Cog.__new__` copies each command fresh per instance, and
`discord.ext.commands.hybrid.HybridAppCommand.__init__` additionally
borrows-then-deletes a command's `__signature__` while building its slash-
command equivalent) reads this same, already-clean `__annotations__` dict,
not the original `Annotated` one -- so unlike a transient `__signature__`
override (verified directly: it does not survive that borrow-then-delete
step, which broke a real bot load in CI), this survives indefinitely.
"""

from __future__ import annotations

import inspect
import math
import types
import typing
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, TypeVar, cast

_MARKER_ATTR = "__corridor_llm_tool__"

F = TypeVar("F", bound=Callable[..., Any])

_JSON_TYPES: dict[type, str] = {str: "string", int: "integer", float: "number", bool: "boolean"}

# Every Red command callback starts `(self, ctx, ...)` -- neither is
# LLM-visible, so the schema is built from whatever comes after them.
_LEADING_PARAMS_TO_SKIP = 2


@dataclass(frozen=True, slots=True)
class ToolDescription:
    """JSON Schema metadata for one `@llm_tool` callback parameter.

    `description` applies to every supported parameter type. `minimum` and
    `maximum` apply only to numeric parameters. `enum` accepts primitive
    values matching the parameter's inferred JSON type. The decorator
    validates combinations at import time and emits the configured fields
    directly into the tool's property schema.
    """

    description: str
    minimum: int | float | None = None
    maximum: int | float | None = None
    enum: tuple[str | int | float | bool, ...] | None = None


@dataclass(frozen=True, slots=True)
class LLMToolSpec:
    """Everything `collect_registered_tools` needs to build a
    `RegisteredTool` from a decorated callback, short of the live cog
    instance and Command object that only exist once the cog is loaded."""

    name: str | None
    description: str
    parameters: dict[str, object]
    required_group: str | None


def llm_tool(
    *,
    name: str | None = None,
    description: str | None = None,
    required_group: str | None = None,
) -> Callable[[F], F]:
    """Mark a Red command callback as a cross-cog, LLM-callable tool.

    Apply directly to the command's own callback -- the innermost
    decorator, right above `async def ...` -- so corridor's
    `CogBase.register_llm_tools()` (called from the owning cog's own
    `cog_load`) can find it and register it into the cross-cog tool
    registry automatically.

    `name` defaults to the Discord command's qualified name with spaces
    replaced by underscores (for example `deskutils time` becomes
    `deskutils_time`). Because the Discord command object does not exist
    until the outer command decorator runs, that default is resolved later
    by the registration adapter. `description` defaults to this callback's
    cleaned docstring; a callback without one must supply it explicitly.

    `parameters`'s JSON Schema is inferred from the callback's own
    signature/type hints, skipping the leading `self`/`ctx` parameters
    every Red command has. Only `str`/`int`/`float`/`bool` (optionally
    `| None`) base types are supported -- anything else raises `TypeError`
    here, at decoration/import time, not later.

    Give a parameter schema metadata by wrapping its type in
    `typing.Annotated` with a `ToolDescription`:

    ```python
    timezone: Annotated[
        str | None,
        ToolDescription("An IANA time zone name, e.g. 'America/New_York'."),
    ] = None
    ```

    `ToolDescription` can also set `minimum`/`maximum` on an `int` or
    `float` parameter, or an `enum` of allowed primitive values matching
    the parameter's type. Invalid combinations raise `TypeError` here, at
    decoration/import time. A raw string inside `Annotated` is not schema
    metadata and is ignored.

    This is safe to write directly on a real Discord command parameter --
    see this module's own docstring for exactly what `@llm_tool` does to
    make that true (mutating `func.__annotations__`, not a transient
    `__signature__` override) and why the more obvious-looking fix wasn't
    enough on its own.

    A callback may return a string-keyed `Mapping[str, object]` containing
    information for the calling LLM. The registration adapter forwards
    that mapping as the tool result. Returning `None` preserves the simple
    `{"status": "ok"}` acknowledgement used by commands with no custom
    result.

    When `required_group` is omitted, corridor uses the Discord command's
    own `can_run(..., check_all_parents=True)` result to decide whether the
    tool is visible to an invoking context. Supplying `required_group`
    preserves the explicit corridor permission-group gate. The callback is
    still invoked directly rather than through Discord dispatch, so
    callbacks should retain their own runtime validation and any explicit
    permission check needed for defense in depth.
    """

    def decorator(func: F) -> F:
        resolved_description = description if description is not None else inspect.getdoc(func)
        if not resolved_description:
            raise TypeError(
                f"llm_tool: {func.__qualname__} has no description or docstring -- "
                "supply description=... or add a callback docstring"
            )
        parameters = infer_parameters(func, strict=True)

        spec = LLMToolSpec(
            name=name,
            description=resolved_description,
            parameters=parameters,
            required_group=required_group,
        )
        setattr(func, _MARKER_ATTR, spec)
        return func

    return decorator


def infer_parameters(func: Callable[..., object], *, strict: bool) -> dict[str, object]:
    """Build the `{"type": "object", "properties": ..., "required": ...}`
    JSON Schema `@llm_tool` always infers from a callback's own signature,
    skipping the leading `self`/`ctx` parameters every Red command has.

    `strict=True` -- what `@llm_tool` itself uses -- raises `TypeError` at
    call time for any parameter whose annotation isn't one of
    `str`/`int`/`float`/`bool` (optionally `| None`, optionally wrapped in
    `Annotated[..., ToolDescription(...)]`): an authoring error the cog's
    own author is right there to fix.

    `strict=False` has no author to hand that error to -- it's for wrapping
    a Discord command nobody wrote `@llm_tool` for. An unsupported
    parameter still gets a schema entry; it just falls back to a generic
    string description (`raw value for <name>, as you would type it in
    Discord`) instead of raising, and its `__annotations__` entry (if any)
    is left untouched since there is no bare type to replace it with.
    """

    hints = typing.get_type_hints(func, include_extras=True)
    params = list(inspect.signature(func).parameters.values())[_LEADING_PARAMS_TO_SKIP:]

    properties: dict[str, object] = {}
    required: list[str] = []
    for param in params:
        annotation = hints.get(param.name, str)
        bare_type, tool_description = _strip_annotated(func, param.name, annotation)
        parameter_type = _parameter_type_for(func, param.name, bare_type, strict=strict)
        if parameter_type is None:
            prop: dict[str, object] = {
                "type": "string",
                "description": f"raw value for {param.name}, as you would type it in Discord",
            }
        else:
            prop = {
                "type": _JSON_TYPES[parameter_type],
                "description": f"value for {param.name}",
            }
            if tool_description is not None:
                prop.update(
                    _tool_description_schema(func, param.name, parameter_type, tool_description)
                )
        properties[param.name] = prop
        if param.default is inspect.Parameter.empty:
            required.append(param.name)
        if parameter_type is not None and bare_type is not annotation:
            # The whole point: every later re-derivation of this
            # callback's signature discord.py ever does -- including
            # ones this decorator has no visibility into, deep inside
            # Cog/HybridCommand construction -- reads this same
            # __annotations__ dict, so it never has a chance to
            # misinterpret `Annotated`'s second argument again.
            func.__annotations__[param.name] = bare_type

    return {"type": "object", "properties": properties, "required": required}


def llm_tool_spec(func: Callable[..., object]) -> LLMToolSpec | None:
    """Read back the spec `@llm_tool` attached to `func`, if any.

    Used by corridor's own scanner, and equally usable by a decorated
    cog's own tests to assert its command really did get tagged correctly
    without needing corridor's adapter-layer scanning machinery.
    """

    return getattr(func, _MARKER_ATTR, None)


def _strip_annotated(
    func: Callable[..., object], param_name: str, annotation: object
) -> tuple[object, ToolDescription | None]:
    """Return an `Annotated` base type and its one `ToolDescription`.

    Other metadata, including the former raw-string shorthand, is ignored.
    The annotation itself is still stripped before discord.py can interpret
    any metadata as a command converter.
    """

    if typing.get_origin(annotation) is typing.Annotated:
        args = typing.get_args(annotation)
        descriptions = [meta for meta in args[1:] if isinstance(meta, ToolDescription)]
        if len(descriptions) > 1:
            raise TypeError(
                f"llm_tool: {func.__qualname__}'s parameter {param_name!r} has more than "
                "one ToolDescription -- supply at most one"
            )
        return args[0], descriptions[0] if descriptions else None
    return annotation, None


def _parameter_type_for(
    func: Callable[..., object], param_name: str, annotation: object, *, strict: bool
) -> type | None:
    """Resolve `annotation` to one of the four supported JSON types.

    In strict mode an unsupported annotation raises `TypeError`, as before.
    In lenient mode it returns `None`, signalling the caller to fall back to
    a generic string property instead."""

    if typing.get_origin(annotation) is types.UnionType:
        args = [arg for arg in typing.get_args(annotation) if arg is not type(None)]
        if len(args) == 1:
            return _parameter_type_for(func, param_name, args[0], strict=strict)
    if not isinstance(annotation, type) or annotation not in _JSON_TYPES:
        if not strict:
            return None
        raise TypeError(
            f"llm_tool: {func.__qualname__}'s parameter {param_name!r} has an unsupported "
            f"type {annotation!r} -- only str/int/float/bool (optionally `| None`, optionally "
            "wrapped in Annotated[..., ToolDescription(...)]) are inferable into a JSON Schema"
        )
    return annotation


def _tool_description_schema(
    func: Callable[..., object],
    param_name: str,
    parameter_type: type,
    metadata: ToolDescription,
) -> dict[str, object]:
    prefix = f"llm_tool: {func.__qualname__}'s parameter {param_name!r}"
    if not isinstance(metadata.description, str):
        raise TypeError(f"{prefix} has a ToolDescription.description that is not a string")

    schema: dict[str, object] = {"description": metadata.description}
    bounds = (("minimum", metadata.minimum), ("maximum", metadata.maximum))
    for bound_name, bound in bounds:
        if bound is None:
            continue
        if parameter_type not in (int, float):
            raise TypeError(f"{prefix} sets {bound_name}, but its type is not numeric")
        if not _is_finite_number(bound):
            raise TypeError(f"{prefix}'s {bound_name} must be a finite int or float")
        schema[bound_name] = bound

    if (
        metadata.minimum is not None
        and metadata.maximum is not None
        and metadata.minimum > metadata.maximum
    ):
        raise TypeError(f"{prefix}'s minimum must not be greater than its maximum")

    if metadata.enum is not None:
        if not isinstance(metadata.enum, tuple):
            raise TypeError(f"{prefix}'s enum must be a tuple")
        if not metadata.enum:
            raise TypeError(f"{prefix}'s enum must contain at least one value")
        for value in metadata.enum:
            if not _enum_value_matches(value, parameter_type):
                raise TypeError(
                    f"{prefix}'s enum value {value!r} does not match {parameter_type.__name__}"
                )
            if isinstance(value, float) and not math.isfinite(value):
                raise TypeError(f"{prefix}'s enum values must be finite")
            numeric_value = cast(int | float, value)
            if metadata.minimum is not None and numeric_value < metadata.minimum:
                raise TypeError(f"{prefix}'s enum value {value!r} is below its minimum")
            if metadata.maximum is not None and numeric_value > metadata.maximum:
                raise TypeError(f"{prefix}'s enum value {value!r} is above its maximum")
        if len(set(metadata.enum)) != len(metadata.enum):
            raise TypeError(f"{prefix}'s enum values must be unique")
        schema["enum"] = list(metadata.enum)

    return schema


def _is_finite_number(value: object) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return not isinstance(value, float) or math.isfinite(value)


def _enum_value_matches(value: object, parameter_type: type) -> bool:
    if parameter_type is float:
        return _is_finite_number(value)
    return type(value) is parameter_type


__all__ = ["LLMToolSpec", "ToolDescription", "infer_parameters", "llm_tool", "llm_tool_spec"]
