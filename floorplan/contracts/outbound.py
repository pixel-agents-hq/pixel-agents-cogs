"""Typed builder for the one outbound message floorplan still owns directly.

Agent-visualization messages (agentCreated, agentToolStart, etc.) moved to
`pixelagents.contracts.outbound` -- import them from there directly. Layout
content is floorplan's own concern (layout/furniture editing), not agent
visualization, so it stays here.
"""

from __future__ import annotations

from typing import Literal, TypedDict

from .layout import RawOfficeLayout


class LayoutLoadedMessage(TypedDict):
    type: Literal["layoutLoaded"]
    layout: RawOfficeLayout | None


def layout_loaded(layout: RawOfficeLayout | None) -> LayoutLoadedMessage:
    return {"type": "layoutLoaded", "layout": layout}
