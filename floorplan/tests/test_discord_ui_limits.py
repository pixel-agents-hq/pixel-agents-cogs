"""Every Floorplan view must respect Discord component limits."""

from __future__ import annotations

import inspect
import unittest
from unittest.mock import MagicMock

import discord

from corridor import ui_limits
from floorplan.adapters import layout_views
from floorplan.adapters.layout_views import LayoutBrowseView, LayoutDetailView
from floorplan.application import CatalogueService
from floorplan.contracts.pixel_index import LayoutDetail, LayoutListResponse


def _service() -> MagicMock:
    return MagicMock(spec=CatalogueService)


def _summary() -> dict[str, object]:
    return {
        "slug": "office",
        "title": "Office",
        "files": {"thumbnail": "/office.png"},
    }


def _browse_view() -> LayoutBrowseView:
    page = LayoutListResponse.model_validate({"total": 1, "layouts": [_summary()]})
    return LayoutBrowseView(
        _service(),
        owner_id=1,
        query=None,
        tag=None,
        sort="newest",
        pages=[page],
        page_index=0,
        api_base="https://api.example.test",
        web_base="https://index.example.test",
    )


def _detail_view() -> LayoutDetailView:
    detail = LayoutDetail.model_validate(
        {**_summary(), "layout": {"version": 1, "cols": 1, "rows": 1, "tiles": [1]}}
    )
    return LayoutDetailView(
        _service(),
        owner_id=1,
        detail=detail,
        api_base="https://api.example.test",
        web_base="https://index.example.test",
    )


class TestLayoutViews(unittest.TestCase):
    def test_factory_registry_covers_every_declared_view(self) -> None:
        discovered = {
            member
            for _, member in inspect.getmembers(layout_views, inspect.isclass)
            if member.__module__ == layout_views.__name__
            and issubclass(member, discord.ui.LayoutView)
        }
        assert discovered == {LayoutBrowseView, LayoutDetailView}

    def test_every_view_passes_component_limits(self) -> None:
        for view in (_browse_view(), _detail_view()):
            violations = ui_limits.check_ui_tree(view)
            self.assertEqual(
                violations,
                [],
                f"{type(view).__name__} violates Discord component limits:\n"
                + ui_limits.format_violations(violations),
            )


if __name__ == "__main__":
    unittest.main()
