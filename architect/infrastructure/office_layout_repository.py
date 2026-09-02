"""Re-exports pixelagents' shared `OfficeLayoutRepository`.

Architect and painter both load/save the same `OfficeStateKind.EDITOR`
aggregate through an identical decode/encode round trip, so the real
implementation lives once in `pixelagents.infrastructure.office_layout_repository`
(pixelagents already owns the Semantic IR domain model and codec) --
this module keeps architect's own existing import path
(`..infrastructure.office_layout_repository`) working unchanged, the same
shim pattern `architect/domain/__init__.py` already uses for `Office`.
"""

from __future__ import annotations

from pixelagents.infrastructure.office_layout_repository import (
    OfficeLayoutRepository,
    SupportsOfficeState,
)

__all__ = ["OfficeLayoutRepository", "SupportsOfficeState"]
