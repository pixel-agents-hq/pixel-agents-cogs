"""Generic field classification + corridor.domain event construction.

Framework-agnostic on purpose (no discord.py import here, matching this
repo's existing convention -- no application/ layer in any cog imports
discord directly, that's adapters' job): a UserSelect-picked
discord.Member is reduced to a plain (discord_user_id, guild_id, is_bot)
tuple by the caller (adapters/views.py) before reaching this module.

`classify()` is the one place a genuinely new *kind* of nested value-object
field (something other than AgentRef, referenced directly rather than
inside a tuple) would need a new branch -- today only AgentRef fits that
shape, so the fallback degrades gracefully (skip, like TUPLE) instead of
crashing, and every existing event's fields fall into one of the four
FieldKinds without any per-event-name code anywhere in this module."""

from __future__ import annotations

import ast
from enum import Enum, auto
from typing import Any

from corridor import domain as corridor_domain

from ..domain import EventSpec, FieldSpec

_KNOWN_SCALARS = {"str", "int", "bool"}
_TRUE_VALUES = {"true", "1", "yes", "y"}
_FALSE_VALUES = {"false", "0", "no", "n"}


class FieldKind(Enum):
    AGENT_REF = auto()  # type_str == "AgentRef" -> UserSelect
    LITERAL = auto()  # type_str is Literal[...] (optionally "| None") -> Select
    TUPLE = auto()  # type_str startswith "tuple[" -> v1: skip, use the dataclass default
    SCALAR = auto()  # str / int / bool (optionally "| None") -> Modal TextInput


def _strip_optional(type_str: str) -> tuple[str, bool]:
    if type_str.endswith(" | None"):
        return type_str[: -len(" | None")], True
    return type_str, False


def classify(field: FieldSpec) -> FieldKind:
    base, _optional = _strip_optional(field.type_str)
    if base == "AgentRef":
        return FieldKind.AGENT_REF
    if base.startswith("Literal["):
        return FieldKind.LITERAL
    if base.startswith("tuple["):
        return FieldKind.TUPLE
    if base in _KNOWN_SCALARS:
        return FieldKind.SCALAR
    # An unrecognized class-name type referenced directly (not AgentRef,
    # not inside a tuple[...]) -- no known-good UI mapping today. Degrade
    # like TUPLE: skip and rely on the field having a default, rather than
    # crashing the whole event picker over one field this classifier
    # doesn't understand yet.
    return FieldKind.TUPLE


def literal_options(type_str: str) -> tuple[str, ...]:
    base, _optional = _strip_optional(type_str)
    inner = base[len("Literal[") : -1]
    # Safe: type_str is corridor/event_catalog.py's own generator output,
    # never user input -- ast.literal_eval, not eval().
    values = ast.literal_eval(f"({inner},)")
    return tuple(str(value) for value in values)


def coerce_scalar(type_str: str, raw: str) -> object:
    base, optional = _strip_optional(type_str)
    raw = raw.strip()
    if raw == "":
        if optional:
            return None
        raise ValueError(f"a value is required for this {base} field")
    if base.startswith("Literal["):
        allowed = literal_options(type_str)
        if raw not in allowed:
            raise ValueError(f"{raw!r} is not one of {', '.join(allowed)}")
        return raw
    if base == "int":
        try:
            return int(raw)
        except ValueError as exc:
            raise ValueError(f"{raw!r} is not a valid integer") from exc
    if base == "bool":
        lowered = raw.lower()
        if lowered in _TRUE_VALUES:
            return True
        if lowered in _FALSE_VALUES:
            return False
        raise ValueError(f"{raw!r} is not a valid true/false value")
    if base == "str":
        return raw
    raise ValueError(f"unsupported scalar type: {type_str}")


def resolve_event_class(name: str) -> type:
    return getattr(corridor_domain, name)  # type: ignore[no-any-return]


def build_event(
    spec: EventSpec,
    *,
    agent_selections: dict[str, tuple[int, int, bool]],
    literal_selections: dict[str, str],
    scalar_inputs: dict[str, str],
) -> object:
    """Constructs corridor.domain.<spec.name>(...) from the three collected
    value maps, applying coerce_scalar per remaining field."""

    kwargs: dict[str, Any] = {}
    for field in spec.fields:
        kind = classify(field)
        if kind is FieldKind.AGENT_REF:
            discord_user_id, guild_id, is_bot = agent_selections[field.name]
            kwargs[field.name] = corridor_domain.AgentRef(
                discord_user_id=discord_user_id, guild_id=guild_id, is_bot=is_bot
            )
        elif kind is FieldKind.LITERAL:
            kwargs[field.name] = literal_selections[field.name]
        elif kind is FieldKind.TUPLE:
            if field.required:
                raise ValueError(
                    f"{spec.name}.{field.name} ({field.type_str}) has no supported input "
                    "path and no default to fall back to"
                )
            # Omit -- the dataclass's own default applies.
        else:  # SCALAR
            value = coerce_scalar(field.type_str, scalar_inputs.get(field.name, ""))
            if value is None and field.required:
                raise ValueError(f"{spec.name}.{field.name} is required")
            kwargs[field.name] = value
    return resolve_event_class(spec.name)(**kwargs)
