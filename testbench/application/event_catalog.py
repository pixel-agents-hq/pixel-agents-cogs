"""Translates corridor.event_catalog.build_contract()'s plain dict into
testbench's own typed EventSpec/FieldSpec shape.

Imports `corridor` directly, never `contracts` -- contracts/ is documented
(docs/corridor.md) as a SHARED_LIBRARY-type package no real cog imports at
runtime. corridor.event_catalog.build_contract() is the same pure
introspection contracts/corridor/generate_corridor_contract.py uses to
regenerate the committed corridor/corridor.yaml, so this module's output is
always exactly what that file says, without reading it as a file."""

from __future__ import annotations

from corridor.event_catalog import build_contract

from ..domain import EventSpec, FieldSpec


def _field_specs(fields: dict[str, dict[str, object]]) -> tuple[FieldSpec, ...]:
    return tuple(
        FieldSpec(
            name=name,
            type_str=str(entry["type"]),
            required="default" not in entry,
            default=entry.get("default"),
        )
        for name, entry in fields.items()
    )


def list_publishable_events() -> tuple[EventSpec, ...]:
    """Every corridor.yaml entry with kind == "event", sorted by name."""

    contract = build_contract()
    events = [
        EventSpec(name=name, fields=_field_specs(entry["fields"]))
        for name, entry in contract["events"].items()
        if entry["kind"] == "event"
    ]
    return tuple(sorted(events, key=lambda spec: spec.name))


def value_object_fields(type_name: str) -> tuple[FieldSpec, ...] | None:
    """A nested value-object's own fields (e.g. AgentRef's 3 fields), for
    generic flattening. None if type_name isn't a known value-object."""

    contract = build_contract()
    entry = contract["events"].get(type_name)
    if entry is None or entry["kind"] != "value-object":
        return None
    return _field_specs(entry["fields"])
