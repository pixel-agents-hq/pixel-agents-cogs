"""Pure business models. Zero framework imports -- this module never imports
discord.py, redbot, or corridor, so it is trivially unit-testable without
any of them installed.

A plain-data mirror of one corridor.yaml event entry -- not corridor's own
domain dataclasses (AgentReplied, AgentPresenceChanged, ...), which this
cog never subclasses or extends. testbench only ever *describes* those
types generically, via FieldSpec/EventSpec, then constructs a real
instance of one by name (see application/event_builder.py)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class FieldSpec:
    """One field of one corridor event, as declared in corridor.yaml.

    `type_str` is the exact string corridor/event_catalog.py renders (e.g.
    "str", "Literal['online', 'idle', 'dnd', 'offline']", "AgentRef",
    "tuple[AgentActivity, ...]") -- application/event_builder.py's
    `classify()` is the one place that interprets it."""

    name: str
    type_str: str
    required: bool
    default: object | None = None


@dataclass(frozen=True, slots=True)
class EventSpec:
    """One publishable corridor event (corridor.yaml entries with
    kind == "event") -- only this event's own fields, in declared order."""

    name: str
    fields: tuple[FieldSpec, ...]
