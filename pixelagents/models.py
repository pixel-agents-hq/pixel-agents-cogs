"""Compatibility exports for the historical Pixel Index model path.

The canonical HTTP contracts now live in :mod:`pixelagents.contracts.pixel_index`.
This module intentionally keeps the old names importable for downstream users
and the monolithic Cog while the rest of the package is extracted.
"""

from pydantic import BaseModel as PydanticBaseModel

from .contracts.pixel_index import (
    LayoutDetail,
    LayoutFiles,
    LayoutListResponse,
    LayoutSummary,
    PublicAuthor,
)

# Kept for the existing contract-lint introspection code, which historically
# discovered model subclasses through ``pixelagents.models.BaseModel``.
BaseModel = PydanticBaseModel

__all__ = [
    "BaseModel",
    "LayoutDetail",
    "LayoutFiles",
    "LayoutListResponse",
    "LayoutSummary",
    "PublicAuthor",
]
