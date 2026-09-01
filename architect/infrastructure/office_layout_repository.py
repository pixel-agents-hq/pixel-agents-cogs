"""Bridges architect's `Office` IR against pixelagents' `OfficeStateFacade`
-- the "editor" aggregate, shared with painter and with `cctv`'s editor
dashboard page (docs/cctv-design.md). architect no longer owns any
Config store of its own for the layout: this module's `office_state`
callable resolves the live `pixelagents` Cog's facade lazily, since
`pixelagents` isn't resolved until `cog_load()` runs but this repository
is constructed in `CogBase.__init__` (same lazy-lookup shape as this
package's `_LazyPixelAgents`).

This is the only place `application/office_layout_service.py` touches
the Pixel Agents adapter; everything above this module speaks `Office`,
never raw JSON.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol

from pixelagents.application.office_state import OfficeLayoutNotSeededError
from pixelagents.infrastructure.furniture_styles import FurnitureStyleManifest
from pixelagents.infrastructure.pixel_agents_adapter import decode

from ..domain import Office


class SupportsEditorOffice(Protocol):
    async def load_editor_office(self, styles: FurnitureStyleManifest) -> Office: ...

    async def set_editor_layout(self, office: Office, styles: FurnitureStyleManifest) -> Any: ...


class OfficeLayoutRepository:
    def __init__(self, office_state: Callable[[], SupportsEditorOffice]) -> None:
        self._office_state = office_state

    async def load(self, styles: FurnitureStyleManifest) -> Office:
        return await self._office_state().load_editor_office(styles)

    def decode_raw(self, raw: dict[str, Any], styles: FurnitureStyleManifest) -> Office:
        """Decode a raw Pixel Agents layout that didn't come from storage --
        e.g. a whole-office payload the in-browser editor sends after a
        drag-and-drop session -- without touching `set_editor_layout()`.
        Mirrors `load()`'s own decode exactly; the caller
        (`OfficeLayoutService.replace_layout`) still has to call `save()`
        separately to persist it."""

        return decode(raw, styles)

    async def save(self, office: Office, styles: FurnitureStyleManifest) -> None:
        await self._office_state().set_editor_layout(office, styles)


__all__ = ["OfficeLayoutNotSeededError", "OfficeLayoutRepository", "SupportsEditorOffice"]
