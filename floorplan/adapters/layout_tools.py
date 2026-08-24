"""Model-facing schema and output helpers for the layout Discord commands."""

from __future__ import annotations

from typing import Annotated, TypeAlias

from corridor.domain import ToolDescription

from ..application import LAYOUT_SORT_CHOICES
from ..contracts.pixel_index import LayoutDetail, LayoutListResponse, LayoutSummary
from .layout_views import absolute_url

SEARCH_TOOL_DESCRIPTION = (
    "Search and display shared Pixel Index office layouts. Call without filters to show "
    "which layouts are available; optionally filter by title/description text or tag and "
    "choose a sort order."
)
PERMISSION_DENIED_MESSAGE = "The invoking member does not have permission to use this tool."
VIEW_TOOL_DESCRIPTION = (
    "Display details and a preview for one Pixel Index office layout by its exact slug. "
    "Use the slug returned by floorplan_layout_search; use 'default' for the default "
    "layout. This displays the layout and does not load it automatically."
)

LayoutQuery: TypeAlias = Annotated[
    str | None,
    ToolDescription(
        "Optional text to search for in layout titles and descriptions. "
        "Omit to list available layouts."
    ),
]
LayoutTag: TypeAlias = Annotated[
    str | None,
    ToolDescription("Optional exact tag used to filter layouts."),
]
LayoutSort: TypeAlias = Annotated[
    str,
    ToolDescription("Sort order for results.", enum=LAYOUT_SORT_CHOICES),
]
LayoutSlug: TypeAlias = Annotated[
    str,
    ToolDescription("Exact Pixel Index layout slug returned by search, e.g. 'default'."),
]


def layout_summary_output(layout: LayoutSummary) -> dict[str, object]:
    """JSON-safe metadata aligned with what LayoutBrowseView exposes."""

    return {
        "slug": layout.slug,
        "title": layout.title or layout.slug,
        "description": layout.description,
        "tags": list(layout.tags),
        "author": layout.author.displayName if layout.author else None,
        "visible_columns": layout.visibleCols,
        "visible_rows": layout.visibleRows,
        "furniture_count": layout.furniture,
    }


def search_input_error(query: object, tag: object, sort: object) -> tuple[str, str] | None:
    """Validate raw model arguments that bypass Discord's converters."""

    if query is not None and not isinstance(query, str):
        return "invalid_query", "Layout search query must be text."
    if tag is not None and not isinstance(tag, str):
        return "invalid_tag", "Layout tag must be text."
    if not isinstance(sort, str):
        return "invalid_sort", "Layout sort order must be text."
    return None


def layout_search_output(
    page: LayoutListResponse,
    *,
    query: str | None,
    tag: str | None,
    sort: str,
) -> dict[str, object]:
    returned = len(page.layouts)
    return {
        "status": "ok",
        "message": (
            f"Displayed {returned} Pixel Index layout(s) in Discord."
            if returned
            else "No layouts found on Pixel Index."
        ),
        "query": query,
        "tag": tag,
        "sort": sort,
        "total": page.total if page.total is not None else returned,
        "returned": returned,
        "has_more": page.nextCursor is not None,
        "layouts": [layout_summary_output(layout) for layout in page.layouts],
    }


def layout_detail_output(
    detail: LayoutDetail,
    *,
    api_base: str,
    web_base: str,
) -> dict[str, object]:
    """Return displayed detail metadata without the potentially large layout blob."""

    output = layout_summary_output(detail)
    files = detail.files
    output.update(
        {
            "area_count": detail.areas,
            "pet_count": detail.pets,
            "seat_count": detail.seats,
            "preview_url": (
                absolute_url(api_base, files.preview) if files and files.preview else None
            ),
            "download_url": (
                absolute_url(api_base, files.layout) if files and files.layout else None
            ),
            "web_url": absolute_url(web_base, f"/layouts/{detail.slug}"),
        }
    )
    return {
        "status": "ok",
        "message": f"Displayed `{detail.title or detail.slug}` in Discord.",
        "layout": output,
    }


def tool_error(error: str, message: str) -> dict[str, object]:
    return {"status": "error", "error": error, "message": message}


__all__ = [
    "PERMISSION_DENIED_MESSAGE",
    "SEARCH_TOOL_DESCRIPTION",
    "VIEW_TOOL_DESCRIPTION",
    "LayoutQuery",
    "LayoutSlug",
    "LayoutSort",
    "LayoutTag",
    "layout_detail_output",
    "layout_search_output",
    "search_input_error",
    "tool_error",
]
