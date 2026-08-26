"""Config-backed storage for architect's `Office` IR.

A thin wrapper around the existing `settings_repository.py`
`layout()`/`set_layout()` methods -- no Pixel JSON schema change needed,
it already stores an opaque JSON blob (`RedArchitectRepository.layout`).
This is the only place `application/office_layout_service.py` touches the
Pixel Agents adapter; everything above this module speaks `Office`, never
raw JSON. See docs/architect-semantic-ir-design.md sections 8 and 10.
"""

from __future__ import annotations

from typing import Any, Protocol

from ..domain.office_ir import Office
from .furniture_styles import FurnitureStyleManifest
from .pixel_agents_adapter import decode, encode


class SupportsLayoutStorage(Protocol):
    async def layout(self) -> dict[str, Any] | None: ...
    async def set_layout(self, layout: dict[str, Any]) -> None: ...


class OfficeLayoutNotSeededError(RuntimeError):
    """Raised when `load()` is called before architect has any stored
    layout at all -- `CogBase._ensure_layout_seeded()` seeds one from
    pixelagents' bundled default the first time the webview build syncs,
    so this should only ever surface if that hasn't run yet."""


class OfficeLayoutRepository:
    def __init__(self, settings_repository: SupportsLayoutStorage) -> None:
        self._settings_repository = settings_repository

    async def load(self, styles: FurnitureStyleManifest) -> Office:
        raw = await self._settings_repository.layout()
        if raw is None:
            raise OfficeLayoutNotSeededError("architect's office layout has not been seeded yet")
        return decode(raw, styles)

    async def save(self, office: Office, styles: FurnitureStyleManifest) -> dict[str, Any]:
        """Encode and persist `office`, returning the raw JSON that was
        stored -- the caller (`OfficeLayoutService`) uses this to
        broadcast `layoutLoaded` to connected webview clients without
        re-encoding."""

        raw = encode(office, styles)
        await self._settings_repository.set_layout(raw)
        return raw


__all__ = ["OfficeLayoutNotSeededError", "OfficeLayoutRepository", "SupportsLayoutStorage"]
