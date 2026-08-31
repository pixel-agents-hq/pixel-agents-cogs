"""Corridor's opaque office-state aggregates -- persisted, revisioned,
and deliberately independent of corridor's own Discord-vocabulary
Pub/Sub domain model (models.py). See docs/cctv-design.md.

Not part of `AgentActivityEvent` / the `Agent*`-prefixed pub/sub catalog
(`corridor/event_catalog.py`) -- `OfficeStateChanged` is a data-mutation
notification, not an agent-activity event, so it gets its own parallel
contract pipeline (`corridor/office_event_catalog.py`,
`corridor/office_state.yaml`) rather than being forced into that
naming convention. See docs/cctv-design.md §2.2 for why this is a
deliberate reversal of an earlier, narrower decision recorded in
docs/painter-design.md §8.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

OfficeStateKind = Literal["discord", "editor"]


@dataclass(frozen=True, slots=True)
class OfficeState:
    """One opaque, revisioned office-state aggregate. `layout`/`seats` are
    plain JSON-compatible dicts -- corridor treats them as opaque; only
    pixelagents' facade (`pixelagents/application/office_state.py`)
    understands their schema. `revision` increments on every successful
    mutation of either field, used to detect stale/duplicate delivery on
    the receiving side, never as a compare-and-set precondition --
    concurrency is deliberately last-write-wins within each field, see
    docs/cctv-design.md §2.3."""

    kind: OfficeStateKind
    layout: dict[str, object]
    seats: dict[str, dict[str, object]]
    revision: int


@dataclass(frozen=True, slots=True)
class OfficeStateChanged:
    """Published after every successful office-state mutation, carrying
    the complete post-write aggregate -- never a diff, so a subscriber
    that missed an intermediate event (or is only just starting to watch)
    is always brought fully current by the next one. See
    docs/cctv-design.md §2.2/§2.5 for delivery rules: synchronous,
    per-subscriber-isolated, and bounded by a 5-second timeout -- a
    genuinely separate dispatch path from `EventBusService`, not a reuse
    of it, since that timeout must stay scoped to office-state only."""

    state: OfficeState


__all__ = ["OfficeState", "OfficeStateChanged", "OfficeStateKind"]
