"""Painter's own copy of `architect/infrastructure/office_layout_repository.py`'s
shape, reading the *same* underlying "editor" aggregate through
pixelagents' `OfficeStateFacade` -- see docs/painter-design.md part A and
docs/cctv-design.md. Resolved independently here and in architect (not
via a live architect reference), both reaching the same shared store.

This is the only place `application/painter_layout_service.py` touches
the Pixel Agents adapter; everything above this module speaks `Office`,
never raw JSON. No `decode_raw()` here: that method exists on architect's
own repository purely for its in-browser editor's whole-office save path,
which painter has no equivalent of.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol

from pixelagents.application.office_state import OfficeLayoutNotSeededError
from pixelagents.domain import Office
from pixelagents.infrastructure.furniture_styles import FurnitureStyleManifest


class SupportsEditorOffice(Protocol):
    async def load_editor_office(self, styles: FurnitureStyleManifest) -> Office: ...

    async def set_editor_layout(self, office: Office, styles: FurnitureStyleManifest) -> Any: ...


class OfficeLayoutRepository:
    def __init__(self, office_state: Callable[[], SupportsEditorOffice]) -> None:
        self._office_state = office_state

    async def load(self, styles: FurnitureStyleManifest) -> Office:
        return await self._office_state().load_editor_office(styles)

    async def save(self, office: Office, styles: FurnitureStyleManifest) -> None:
        await self._office_state().set_editor_layout(office, styles)


__all__ = ["OfficeLayoutNotSeededError", "OfficeLayoutRepository", "SupportsEditorOffice"]
