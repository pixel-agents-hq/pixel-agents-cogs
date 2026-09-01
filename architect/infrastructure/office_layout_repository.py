"""Semantic IR adapter over Pixelagents' revisioned editor aggregate."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Protocol

from corridor.domain import OfficeState, OfficeStateKind
from pixelagents.infrastructure.furniture_styles import FurnitureStyleManifest
from pixelagents.infrastructure.pixel_agents_adapter import decode, encode

from ..domain import Office


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
