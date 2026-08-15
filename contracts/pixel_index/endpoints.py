"""Endpoint metadata for the office-cogs -> Pixel Index contract.

Pairs each endpoint pixelagents.py actually calls with the pydantic model
(if any, from pixelagents/models.py) that describes the response shape it
depends on. Only this bit — which endpoints, what params, how they chain —
is hand-maintained; the response schemas themselves are generated from the
models at build time by generate_contract.py, so they can't drift from what
pixelagents.py actually parses.

Update this alongside pixelagents.py whenever it starts/stops calling an
endpoint or changes how it chains requests together.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Type

from pydantic import BaseModel

from pixelagents.models import LayoutDetail, LayoutListResponse


@dataclass
class EndpointContract:
    name: str
    method: str
    path: str
    expect_status: int = 200
    query: Optional[dict] = None
    path_params: Optional[dict] = None
    requires_var: Optional[str] = None
    derive: Optional[dict] = None
    model: Optional[Type[BaseModel]] = None


ENDPOINTS = [
    EndpointContract(
        name="health",
        method="GET",
        path="/health",
        # _check_pixel_index_health only checks the HTTP status, no body is
        # parsed by the bot, so there's no model/schema for this one.
    ),
    EndpointContract(
        name="list_layouts",
        method="GET",
        path="/api/v1/layouts",
        query={"sort": "newest", "limit": 1},
        model=LayoutListResponse,
        # Feeds layout_detail below with a real slug instead of a hardcoded
        # one that could disappear from a given environment.
        derive={"layout_slug": "layouts[0].slug"},
    ),
    EndpointContract(
        name="layout_detail",
        method="GET",
        path="/api/v1/layouts/{slug}",
        path_params={"slug": "$layout_slug"},
        # Skipped, not failed, if the environment has no published layouts.
        requires_var="layout_slug",
        model=LayoutDetail,
    ),
]
