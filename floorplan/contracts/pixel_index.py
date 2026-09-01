"""Consumer-owned contracts for the Pixel Index HTTP API.

Only the fields used by office-cogs are modeled.  Required fields are read
unconditionally by the application; optional fields mirror its defensive
fallbacks.  Extra response fields remain ignored so Pixel Index can evolve
without breaking this consumer.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class PublicAuthor(BaseModel):
    model_config = ConfigDict(extra="ignore")

    displayName: str | None = None


class LayoutFiles(BaseModel):
    model_config = ConfigDict(extra="ignore")

    thumbnail: str | None = None
    preview: str | None = None
    layout: str | None = None


class LayoutSummary(BaseModel):
    """Fields read from each entry in GET /api/v1/layouts, and inherited by
    LayoutDetail since GET /api/v1/layouts/{slug} returns everything a
    summary has plus the full layout blob."""

    model_config = ConfigDict(extra="ignore")

    slug: str
    title: str | None = None
    description: str | None = None
    tags: list[str] = Field(default_factory=list)
    author: PublicAuthor | None = None
    files: LayoutFiles | None = None
    visibleCols: int | None = None
    visibleRows: int | None = None
    furniture: int | None = None
    areas: int | None = None
    pets: int | None = None
    seats: int | None = None


class LayoutListResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    layouts: list[LayoutSummary] = Field(default_factory=list)
    total: int | None = None
    nextCursor: str | None = None


class LayoutDetail(LayoutSummary):
    """GET /api/v1/layouts/{slug}. `layout` is the opaque Pixel Agents
    layout blob (pixel-index itself treats it as additionalProperties: true).
    Pixelagents validates the value when Floorplan applies it, so this HTTP
    boundary keeps it untyped rather than duplicating that structure."""

    layout: dict[str, Any]
