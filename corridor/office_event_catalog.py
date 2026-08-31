"""Introspects corridor's office-state domain model (`OfficeState`/
`OfficeStateChanged`, `corridor/domain/office_state.py`) into a plain-data
schema -- the single source of truth for `corridor/office_state.yaml`.

Deliberately parallel to, never merged with, `event_catalog.py`'s
`Agent*`-prefixed pub/sub catalog: office-state is a data-mutation
notification, not an agent-activity event (see docs/cctv-design.md §2.2,
which also records this as a considered reversal of the narrower
decision in docs/painter-design.md §8). The introspection body below is
intentionally a near-duplicate of `event_catalog.py::build_contract`'s,
not a shared helper -- these are two independently-evolving contracts,
the same "duplicated, not shared" precedent this repo already applies to
e.g. floorplan's and architect's `WebviewAssetProvider`.

Pure: zero discord/redbot/yaml imports, same as `event_catalog.py`.
"""

from __future__ import annotations

import dataclasses
import types
import typing
from typing import Any

from . import domain as corridor_domain


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


def build_office_contract() -> dict[str, Any]:
    events: dict[str, Any] = {}
    # Every corridor.domain name starting with "Office" belongs to this
    # separate contract -- OfficeState/OfficeStateChanged only, never the
    # Agent*-prefixed pub/sub domain model event_catalog.py already owns.
    for name in sorted(n for n in corridor_domain.__all__ if n.startswith("Office")):
        obj = getattr(corridor_domain, name)
        if not (isinstance(obj, type) and dataclasses.is_dataclass(obj)):
            continue  # skips OfficeStateKind, a Literal type alias, not a dataclass
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
            "kind": "value-object" if name == "OfficeState" else "event",
            "fields": fields,
        }
    return {
        "version": 1,
        "status": "implemented",
        "source_doc": "docs/cctv-design.md",
        "events": events,
    }
