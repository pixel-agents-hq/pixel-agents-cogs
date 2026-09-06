"""Smoke tests for the Components V2 LLM-model picker
(`corridor/adapters/llm_settings_panel.py`): it constructs without error,
respects Discord's component limits, gates on the opening owner, and its
select/refresh callbacks round-trip through the Cog's public API. Deep
caching/degradation coverage lives in `test_model_catalog_service.py`
instead -- this layer is thin by design (see test_settings_ui.py's own
docstring for the same split)."""

from __future__ import annotations

import unittest

import discord

from corridor import ui_limits

from ..adapters.llm_settings_panel import MAX_SELECT_OPTIONS, LLMModelPanelView
from ..corridor import Corridor
from ..domain import LLMSettings, ModelCatalogResult
from ..testing import followup_messages, refreshed_view
from .conftest import FakeBot, FakeGuild


def _settings(*, model: str | None = "chatgpt/gpt-6-astra") -> LLMSettings:
    return LLMSettings(
        llm_base_url="https://litellm.example/", llm_api_key="sk-test", llm_model=model
    )


def _catalog(
    models: tuple[str, ...] = ("chatgpt/gpt-6-astra", "gpt-4o"),
    *,
    stale: bool = False,
    error: str | None = None,
) -> ModelCatalogResult:
    return ModelCatalogResult(models=models, stale=stale, error=error)


class TestLLMModelPanelConstruction(unittest.TestCase):
    def test_view_builds_without_error(self) -> None:
        view = LLMModelPanelView(_settings(), _catalog(), owner_id=1)

        self.assertTrue(view.children)

    def test_empty_catalog_disables_the_select_instead_of_erroring(self) -> None:
        view = LLMModelPanelView(_settings(model=None), _catalog(models=()), owner_id=1)

        container = view.children[0]
        select = next(
            child.children[0]
            for child in container.children
            if getattr(child, "children", None) and isinstance(child.children[0], discord.ui.Select)
        )
        self.assertTrue(select.disabled)

    def test_current_model_is_preselected(self) -> None:
        view = LLMModelPanelView(_settings(model="gpt-4o"), _catalog(), owner_id=1)

        container = view.children[0]
        select = next(
            child.children[0]
            for child in container.children
            if getattr(child, "children", None) and isinstance(child.children[0], discord.ui.Select)
        )
        selected = [option.value for option in select.options if option.default]
        self.assertEqual(selected, ["gpt-4o"])

    def test_catalog_error_is_surfaced_in_the_panel_text(self) -> None:
        view = LLMModelPanelView(
            _settings(), _catalog(stale=True, error="upstream 503"), owner_id=1
        )

        container = view.children[0]
        text = container.children[0].content
        self.assertIn("upstream 503", text)
        self.assertIn("last known list", text)

    def test_respects_discord_component_limits_at_the_option_cap(self) -> None:
        models = tuple(f"model-{i}" for i in range(MAX_SELECT_OPTIONS + 10))
        view = LLMModelPanelView(_settings(model=None), _catalog(models=models), owner_id=1)

        violations = ui_limits.check_ui_tree(view)

        self.assertEqual(violations, [], ui_limits.format_violations(violations))


class TestLLMModelPanelInteractionCheck(unittest.IsolatedAsyncioTestCase):
    async def test_a_different_user_is_rejected(self) -> None:
        view = LLMModelPanelView(_settings(), _catalog(), owner_id=1)
        interaction = discord.Interaction()
        interaction.user = type("U", (), {"id": 2})()

        allowed = await view.interaction_check(interaction)

        self.assertFalse(allowed)

    async def test_the_opening_owner_is_allowed(self) -> None:
        view = LLMModelPanelView(_settings(), _catalog(), owner_id=1)
        interaction = discord.Interaction()
        interaction.user = type("U", (), {"id": 1})()

        allowed = await view.interaction_check(interaction)

        self.assertTrue(allowed)


class TestLLMModelPanelCallbacks(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.bot = FakeBot(owner_ids=frozenset({1}))
        self.guild = FakeGuild(guild_id=10)
        self.bot.register_guild(self.guild)
        self.corridor = Corridor(bot=self.bot)
        self.bot.add_cog(self.corridor)

    async def test_selecting_a_model_persists_it_and_refreshes(self) -> None:
        view = LLMModelPanelView(_settings(), _catalog(), owner_id=1)
        container = view.children[0]
        select = next(
            child.children[0]
            for child in container.children
            if getattr(child, "children", None) and isinstance(child.children[0], discord.ui.Select)
        )
        select.values = ["gpt-4o"]

        interaction = discord.Interaction(guild=self.guild, client=self.bot)
        await select.callback(interaction)

        settings = await self.corridor.llm_settings()
        self.assertEqual(settings.llm_model, "gpt-4o")
        refreshed = refreshed_view(interaction)
        self.assertIsInstance(refreshed, LLMModelPanelView)
        self.assertEqual(refreshed.settings.llm_model, "gpt-4o")
        self.assertTrue(any("gpt-4o" in str(m) for m in followup_messages(interaction)))

    async def test_refresh_button_forces_a_fresh_fetch(self) -> None:
        calls: list[bool] = []
        original = self.corridor.model_catalog

        async def spy_model_catalog(*, force_refresh: bool = False) -> ModelCatalogResult:
            calls.append(force_refresh)
            return await original(force_refresh=force_refresh)

        self.corridor.model_catalog = spy_model_catalog  # type: ignore[method-assign]

        view = LLMModelPanelView(_settings(), _catalog(), owner_id=1)
        container = view.children[0]
        refresh_button = next(
            child.children[0]
            for child in container.children
            if getattr(child, "children", None)
            and isinstance(child.children[0], discord.ui.Button)
            and "Refresh" in (child.children[0].label or "")
        )

        interaction = discord.Interaction(guild=self.guild, client=self.bot)
        await refresh_button.callback(interaction)

        self.assertIn(True, calls)
        self.assertIsInstance(refreshed_view(interaction), LLMModelPanelView)


if __name__ == "__main__":
    unittest.main()
