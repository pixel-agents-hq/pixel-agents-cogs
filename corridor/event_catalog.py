"""Introspects corridor's own Pub/Sub domain model (`corridor/domain/models.py`)
into a plain-data schema -- the single source of truth for both
`corridor/corridor.yaml` (the committed, CI-checked contract) and any
runtime consumer that wants to build tooling generically off the event set
without hardcoding per dataclass (e.g. testbench's Discord UI).

Pure: zero discord/redbot/yaml imports. `contracts/` is documented
(`docs/corridor.md`) as a CI-only package other cogs never import at
runtime -- this module is what lets a real cog (testbench) get the same
schema `contracts/corridor/generate_corridor_contract.py` regenerates
`corridor.yaml` from, without depending on `contracts` at all.
"""

from __future__ import annotations

import dataclasses
import types
import typing
from typing import Any

from . import domain as corridor_domain

# AgentRef and AgentActivity are referenced BY other events' fields, never
# published/subscribed to on their own.
_VALUE_OBJECTS = {"AgentRef", "AgentActivity"}


def _type_name(annotation: Any) -> str:
    origin = typing.get_origin(annotation)
    if origin is typing.Literal:
        return f"Literal[{', '.join(repr(v) for v in typing.get_args(annotation))}]"
    if origin is tuple:
        item_type, _ellipsis = typing.get_args(annotation)
        return f"tuple[{_type_name(item_type)}, ...]"
    if origin is types.UnionType:
        args = [a for a in typing.get_args(annotation) if a is not type(None)]
        if len(args) == 1:
            return f"{_type_name(args[0])} | None"
        return " | ".join(_type_name(a) for a in args)
    if isinstance(annotation, type):
        return annotation.__name__
    return str(annotation)


def build_contract() -> dict[str, Any]:
    events: dict[str, Any] = {}
    # Every corridor.domain name starting with "Agent" is part of the
    # pub/sub domain model -- corridor's other domain types (GuildSettings,
    # PermissionGroupDef, ReplyField, ...) are a different contract's
    # concern entirely and deliberately excluded.
    for name in sorted(n for n in corridor_domain.__all__ if n.startswith("Agent")):
        obj = getattr(corridor_domain, name)
        if not (isinstance(obj, type) and dataclasses.is_dataclass(obj)):
            continue  # skips AgentActivityEvent, the union alias
        hints = typing.get_type_hints(obj)
        fields: dict[str, Any] = {}
        for field in dataclasses.fields(obj):
            entry: dict[str, Any] = {"type": _type_name(hints[field.name])}
            if field.default is not dataclasses.MISSING:
                entry["default"] = (
                    list(field.default) if isinstance(field.default, tuple) else field.default
                )
            fields[field.name] = entry
        events[name] = {
            "kind": "value-object" if name in _VALUE_OBJECTS else "event",
            "fields": fields,
        }
    return {
        "version": 1,
        "status": "implemented",
        "source_doc": "docs/corridor-pubsub-design.md",
        "events": events,
    }
