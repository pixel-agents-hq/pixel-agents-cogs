"""Every Modal/View floorplan defines must respect Discord's component
limits -- see `corridor/ui_limits.py` for what's checked and why
(discord.py itself never validates these; a violation only surfaces at
runtime as an opaque HTTPException / "didn't respond in time").

Mirrors `corridor/tests/test_discord_ui_limits.py`: a hand-maintained
factory registry cross-checked against every LayoutView subclass actually
declared in `layout_views.py` (the only interactive views floorplan
defines now that `settings_panel.py`'s dashboard-configuration UI moved
to `cctv`, see docs/cctv-design.md), so a new one added without a
factory fails the completeness test instead of silently going unchecked.
"""

from __future__ import annotations

import inspect
import unittest

import discord

from corridor import ui_limits
from floorplan.adapters import layout_views
from floorplan.adapters.layout_views import LayoutBrowseView, LayoutDetailView
from floorplan.models import LayoutDetail, LayoutListResponse
from floorplan.tests.test_floorplan import _layout_detail, _layout_summary, _make_cog


def _layout_browse_view() -> LayoutBrowseView:
    cog = _make_cog()
    page = LayoutListResponse.model_validate(
        {"total": 1, "layouts": [_layout_summary("office", "Office")], "nextCursor": None}
    )
    return LayoutBrowseView(
        cog._catalogue_service,
        owner_id=1,
        query=None,
        tag=None,
        sort="newest",
        pages=[page],
        page_index=0,
        api_base="https://pixel-index-api-staging.nntin.xyz",
        web_base="https://pixel-index.vercel.app",
    )


def _layout_detail_view() -> LayoutDetailView:
    cog = _make_cog()
    detail = LayoutDetail.model_validate(_layout_detail("office", title="Office"))
    return LayoutDetailView(
        cog._catalogue_service,
        owner_id=1,
        detail=detail,
        api_base="https://pixel-index-api-staging.nntin.xyz",
        web_base="https://pixel-index.vercel.app",
        back=None,
    )


class TestFactoryRegistryIsComplete(unittest.IsolatedAsyncioTestCase):
    """Fails loudly if a new View is added without a factory below."""

    async def asyncSetUp(self) -> None:
        self.view_factories: dict[type, list[object]] = {
            LayoutBrowseView: [_layout_browse_view()],
            LayoutDetailView: [_layout_detail_view()],
        }

    def _discovered_subclasses(self, module: object, base: type) -> set[type]:
        return {
            member
            for _, member in inspect.getmembers(module, inspect.isclass)
            if member.__module__ == module.__name__ and issubclass(member, base)
        }

    def test_every_view_has_a_factory(self) -> None:
        discovered = self._discovered_subclasses(layout_views, discord.ui.LayoutView)
        missing = discovered - set(self.view_factories)
        self.assertFalse(
            missing,
            f"No ui_limits factory registered for: {[c.__name__ for c in missing]}. "
            "Add coverage to test_discord_ui_limits.py.",
        )

    def test_every_registered_view_instance_passes(self) -> None:
        for view_type, instances in self.view_factories.items():
            for instance in instances:
                with self.subTest(view=view_type.__name__):
                    violations = ui_limits.check_ui_tree(instance)
                    self.assertEqual(
                        violations,
                        [],
                        f"{view_type.__name__} violates Discord component limits:\n"
                        + ui_limits.format_violations(violations),
                    )


if __name__ == "__main__":
    unittest.main()
