"""Pydantic models for the slice of the Pixel Index API pixelagents.py reads.

These are the single source of truth for what office-cogs expects from
Pixel Index's layout list/detail responses — used both to validate real
responses at runtime (see _pixel_index_search/_pixel_index_layout in
pixelagents.py) and to generate contracts/pixel-index/contract.yaml for the
CI contract check (see contracts/pixel-index/generate_contract.py). Only
pydantic is imported here on purpose: this module must stay importable from
the lightweight CI job without discord.py/redbot installed.

Fields the bot reads defensively via `.get(key, default)` are modeled as
optional, matching that tolerance; fields it depends on unconditionally are
required. Keep this in sync with pixelagents.py's actual field access when
either changes.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


class PublicAuthor(BaseModel):
    model_config = ConfigDict(extra="ignore")

    displayName: Optional[str] = None


class LayoutFiles(BaseModel):
    model_config = ConfigDict(extra="ignore")

    thumbnail: Optional[str] = None
    preview: Optional[str] = None
    layout: Optional[str] = None


class LayoutSummary(BaseModel):
    """Fields read from each entry in GET /api/v1/layouts, and inherited by
    LayoutDetail since GET /api/v1/layouts/{slug} returns everything a
    summary has plus the full layout blob."""

    model_config = ConfigDict(extra="ignore")

    slug: str
    title: Optional[str] = None
    description: Optional[str] = None
    tags: List[str] = Field(default_factory=list)
    author: Optional[PublicAuthor] = None
    files: Optional[LayoutFiles] = None
    visibleCols: Optional[int] = None
    visibleRows: Optional[int] = None
    furniture: Optional[int] = None
    areas: Optional[int] = None
    pets: Optional[int] = None
    seats: Optional[int] = None


class LayoutListResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    layouts: List[LayoutSummary] = Field(default_factory=list)
    total: Optional[int] = None
    nextCursor: Optional[str] = None


class LayoutDetail(LayoutSummary):
    """GET /api/v1/layouts/{slug}. `layout` is the opaque Pixel Agents
    layout blob (pixel-index itself treats it as additionalProperties: true)
    — _validate_layout() in pixelagents.py enforces its own required shape,
    so it's kept untyped here rather than duplicating that structure."""

    layout: Dict[str, Any]
