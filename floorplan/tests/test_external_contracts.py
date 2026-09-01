"""Tests for the consumer-owned Pixel Index HTTP contract."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from floorplan.contracts.pixel_index import (
    LayoutDetail,
    LayoutFiles,
    LayoutListResponse,
    PublicAuthor,
)


def test_list_requires_slug_and_ignores_future_fields() -> None:
    response = LayoutListResponse.model_validate(
        {
            "layouts": [
                {
                    "slug": "cozy-office",
                    "author": {"displayName": "Builder", "future": True},
                    "files": {"layout": "layout.json", "future": True},
                    "futureSummaryField": {"nested": True},
                }
            ],
            "total": 1,
            "futurePageField": "ignored",
        }
    )

    assert response.layouts[0].slug == "cozy-office"
    assert response.layouts[0].author == PublicAuthor(displayName="Builder")
    assert response.layouts[0].files == LayoutFiles(layout="layout.json")
    assert response.layouts[0].model_extra is None

    with pytest.raises(ValidationError):
        LayoutListResponse.model_validate({"layouts": [{"title": "No slug"}]})


def test_detail_requires_and_preserves_the_opaque_layout_blob() -> None:
    raw_layout = {"version": 1, "future": [1, {"nested": True}]}
    detail = LayoutDetail.model_validate({"slug": "one", "layout": raw_layout})

    assert detail.layout == raw_layout
    with pytest.raises(ValidationError):
        LayoutDetail.model_validate({"slug": "one"})
