"""Semantic IR adapter over Corridor's revisioned editor aggregate.

Shared by architect (structural edits) and painter (color-only edits) --
both load/save the same `OfficeStateKind.EDITOR` aggregate through this
same decode/encode round trip, so it lives once here (pixelagents already
owns the Semantic IR domain model and codec, see `pixel_agents_adapter.py`)
rather than as a hand-kept-in-sync copy in each of the two cogs that
consume it. Each cog's own `infrastructure/office_layout_repository.py`
re-exports this module's names for its existing call sites -- the same
shim pattern `architect/domain/__init__.py` already uses for `Office`
itself.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Protocol

from corridor.domain import OfficeState, OfficeStateKind

from ..domain import Office
from .furniture_styles import FurnitureStyleManifest
from .pixel_agents_adapter import decode, encode


class SupportsOfficeState(Protocol):
    async def office_state(self, kind: OfficeStateKind) -> OfficeState: ...

    async def set_office_layout(
        self,
        kind: OfficeStateKind,
        layout: Mapping[str, object],
    ) -> OfficeState: ...


class OfficeLayoutRepository:
    def __init__(self, pixelagents: Callable[[], SupportsOfficeState]) -> None:
        self._pixelagents = pixelagents

    async def load(self, styles: FurnitureStyleManifest) -> Office:
        state = await self._pixelagents().office_state(OfficeStateKind.EDITOR)
        return decode(state.layout, styles)

    async def save(self, office: Office, styles: FurnitureStyleManifest) -> None:
        raw = encode(office, styles)
        await self._pixelagents().set_office_layout(OfficeStateKind.EDITOR, raw)


__all__ = ["OfficeLayoutRepository", "SupportsOfficeState"]
