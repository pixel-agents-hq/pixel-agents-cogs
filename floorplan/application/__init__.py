"""Application services coordinating Floorplan use cases.

OfficeService/PresenceService moved to `pixelagents.application` and are
no longer floorplan's own concern at all -- floorplan mirrors no
presence and hosts no dashboard of its own (docs/cctv-design.md).
SettingsService/TaskSupervisor (dashboard/WebSocket-only) were retired
along with the code that used them.
"""

from .catalogue import (
    LAYOUT_SEARCH_PAGE_SIZE,
    LAYOUT_SORT_CHOICES,
    CatalogueBases,
    CatalogueError,
    CatalogueErrorCode,
    CatalogueResult,
    CatalogueService,
)

__all__ = [
    "LAYOUT_SEARCH_PAGE_SIZE",
    "LAYOUT_SORT_CHOICES",
    "CatalogueBases",
    "CatalogueError",
    "CatalogueErrorCode",
    "CatalogueResult",
    "CatalogueService",
]
