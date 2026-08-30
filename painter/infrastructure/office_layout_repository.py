"""Config-backed storage for the shared `Office` IR -- painter's own copy
of `architect/infrastructure/office_layout_repository.py`'s shape, reading
the *same* underlying store (see docs/painter-design.md part A):
`RedOfficeLayoutSettings` is constructed independently here and in
architect, both resolving to the same pixelagents-owned Config document
by identifier + `cog_name`, not by sharing a Python object.

This is the only place `application/painter_layout_service.py` touches
the Pixel Agents adapter; everything above this module speaks `Office`,
never raw JSON.
"""

from __future__ import annotations

from typing import Any, Protocol

from pixelagents.domain import Office
from pixelagents.infrastructure.furniture_styles import FurnitureStyleManifest
from pixelagents.infrastructure.pixel_agents_adapter import decode, encode


class SupportsLayoutStorage(Protocol):
    async def layout(self) -> dict[str, Any] | None: ...
    async def set_layout(self, layout: dict[str, Any]) -> None: ...


class OfficeLayoutNotSeededError(RuntimeError):
    """Raised when `load()` is called before the shared office layout has
    been seeded at all -- architect's own `CogBase._ensure_layout_seeded()`
    seeds it from pixelagents' bundled default the first time its webview
    build syncs, so this should only ever surface if that hasn't run yet
    (e.g. painter loaded before architect ever has)."""


class OfficeLayoutRepository:
    def __init__(self, settings_repository: SupportsLayoutStorage) -> None:
        self._settings_repository = settings_repository

    async def load(self, styles: FurnitureStyleManifest) -> Office:
        raw = await self._settings_repository.layout()
        if raw is None:
            raise OfficeLayoutNotSeededError("the shared office layout has not been seeded yet")
        return decode(raw, styles)

    async def save(self, office: Office, styles: FurnitureStyleManifest) -> dict[str, Any]:
        """Encode and persist `office`, returning the raw JSON that was
        stored."""

        raw = encode(office, styles)
        await self._settings_repository.set_layout(raw)
        return raw


__all__ = ["OfficeLayoutNotSeededError", "OfficeLayoutRepository", "SupportsLayoutStorage"]
