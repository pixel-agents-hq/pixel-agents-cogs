"""Every Modal/View pixelagents defines must respect Discord's component
limits -- see `ui_limits.py` at the repo root for what's checked and why
(discord.py itself never validates these; a violation only surfaces at
runtime as an opaque HTTPException / "didn't respond in time").

Mirrors `corridor/tests/test_discord_ui_limits.py`: a hand-maintained
factory registry cross-checked against every Modal/LayoutView subclass
actually declared in `settings_panel.py`/`layout_views.py`, so a new one
added without a factory fails the completeness test instead of silently
going unchecked.
"""

from __future__ import annotations

import inspect
import unittest

import discord

import ui_limits
from pixelagents.adapters import layout_views, settings_panel
from pixelagents.adapters.layout_views import LayoutBrowseView, LayoutDetailView
from pixelagents.adapters.settings_panel import SettingsPanelView, SettingsValueModal
from pixelagents.models import LayoutDetail, LayoutListResponse
from pixelagents.tests.test_pixelagents import _layout_detail, _layout_summary, _make_cog
from pixelagents.tests.test_settings_panel import (
    global_settings,
    guild_settings,
    interaction,
    settings_service,
)


def _settings_panel() -> SettingsPanelView:
    return SettingsPanelView(
        settings_service(),
        owner_id=1,
        guild_id=42,
        global_settings=global_settings(),
        guild_settings=guild_settings(),
        runtime=None,
    )


async def _shown_modal(panel: SettingsPanelView, show: str) -> SettingsValueModal:
    """Call one of the panel's `_show_*_modal` methods and capture the
    modal instance it hands to `interaction.response.send_modal`."""
    fake_interaction = interaction()
    await getattr(panel, show)(fake_interaction)
    return fake_interaction.response.send_modal.call_args.args[0]


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
    """Fails loudly if a new Modal/View is added without a factory below."""

    async def asyncSetUp(self) -> None:
        panel = _settings_panel()
        self.modal_factories: dict[type, list[object]] = {
            SettingsValueModal: [
                await _shown_modal(panel, "_show_port_modal"),
                await _shown_modal(panel, "_show_delay_modal"),
                await _shown_modal(panel, "_show_api_url_modal"),
                await _shown_modal(panel, "_show_web_url_modal"),
            ],
        }
        self.view_factories: dict[type, list[object]] = {
            SettingsPanelView: [panel],
            LayoutBrowseView: [_layout_browse_view()],
            LayoutDetailView: [_layout_detail_view()],
        }

    def _discovered_subclasses(self, module: object, base: type) -> set[type]:
        return {
            member
            for _, member in inspect.getmembers(module, inspect.isclass)
            if member.__module__ == module.__name__ and issubclass(member, base)
        }

    def test_every_modal_has_a_factory(self) -> None:
        discovered = self._discovered_subclasses(settings_panel, discord.ui.Modal)
        missing = discovered - set(self.modal_factories)
        self.assertFalse(
            missing,
            f"No ui_limits factory registered for: {[c.__name__ for c in missing]}. "
            "Add coverage to test_discord_ui_limits.py.",
        )

    def test_every_view_has_a_factory(self) -> None:
        discovered = self._discovered_subclasses(
            settings_panel, discord.ui.LayoutView
        ) | self._discovered_subclasses(layout_views, discord.ui.LayoutView)
        missing = discovered - set(self.view_factories)
        self.assertFalse(
            missing,
            f"No ui_limits factory registered for: {[c.__name__ for c in missing]}. "
            "Add coverage to test_discord_ui_limits.py.",
        )

    def test_every_registered_modal_instance_passes(self) -> None:
        for modal_type, instances in self.modal_factories.items():
            for instance in instances:
                with self.subTest(modal=modal_type.__name__):
                    violations = ui_limits.check_ui_tree(instance)
                    self.assertEqual(
                        violations,
                        [],
                        f"{modal_type.__name__} violates Discord component limits:\n"
                        + ui_limits.format_violations(violations),
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
