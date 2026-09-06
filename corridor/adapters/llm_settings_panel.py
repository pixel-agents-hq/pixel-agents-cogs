"""Components V2 panel: bot owner picks the shared LLM model from LiteLLM's
live catalogue, per `[p]corridor llm model` (no argument). Bot-owner-only
and global (`LLMSettings` is `register_global`, not per-guild) -- unlike
`settings_ui.py`'s `SharedSettingsView`, which is a guild admin's panel
over per-guild `Config`, so this stays its own module rather than a
fragment mounted there.

`interaction_check` compares against the `owner_id` captured when the
panel was opened (already gated by `@commands.is_owner()` on
`[p]corridor llm model`), not a live re-check of bot ownership -- same
shape as toolbox's `ToolSelectionView`/`ToolGuildOverrideView`
(`toolbox/adapters/tool_panel.py`).

Selecting a model calls the same `set_llm_model` the
`[p]corridor llm model <name>` text-command path uses -- this is a second
entry point onto the identical setter, not a parallel persistence path.
The model list itself comes from `CogBase.model_catalog()`
(`ModelCatalogService`), which fronts a cached, best-effort LiteLLM
`/v1/models` fetch and degrades to the currently-configured model (or an
empty, disabled picker) on any upstream failure -- see
`corridor/application/model_catalog_service.py`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

import discord

from ..domain import LLMSettings, ModelCatalogResult

if TYPE_CHECKING:
    from redbot.core.bot import Red

    from .cog_base import CogBase

# A single discord.ui.Select maxes out at 25 options -- the catalogue is
# truncated to fit rather than erroring, same shape as settings_ui.py's
# CURATED_PERMISSIONS comment.
MAX_SELECT_OPTIONS = 25


def _get_corridor(interaction: discord.Interaction) -> CogBase:
    bot = cast("Red", interaction.client)
    corridor = bot.get_cog("Corridor")
    if corridor is None:
        raise RuntimeError("Corridor is not loaded.")
    return cast("CogBase", corridor)


async def _refresh(
    interaction: discord.Interaction,
    owner_id: int,
    *,
    force_refresh: bool = False,
    confirmation: str | None = None,
) -> None:
    corridor = _get_corridor(interaction)
    settings = await corridor.llm_settings()
    catalog = await corridor.model_catalog(force_refresh=force_refresh)
    view = LLMModelPanelView(settings, catalog, owner_id)
    if interaction.response.is_done():
        await interaction.edit_original_response(view=view)
    else:
        await interaction.response.edit_message(view=view)
    if confirmation:
        await interaction.followup.send(confirmation, ephemeral=True)


class LLMModelPanelView(discord.ui.LayoutView):  # type: ignore[misc, unused-ignore]
    def __init__(self, settings: LLMSettings, catalog: ModelCatalogResult, owner_id: int) -> None:
        super().__init__(timeout=180)
        self.settings = settings
        self.catalog = catalog
        self.owner_id = owner_id
        self._build()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "Only the bot owner who opened this panel can use these controls.",
                ephemeral=True,
            )
            return False
        return True

    def _build(self) -> None:
        lines = [
            "**Shared LLM model**",
            f"Endpoint: `{self.settings.llm_base_url}`",
            f"Current model: `{self.settings.llm_model or '(not set)'}`",
        ]
        if self.catalog.error:
            served_as = (
                "showing the last known list" if self.catalog.stale else "showing a fallback"
            )
            lines.append(
                f"⚠️ Could not refresh the model list from LiteLLM ({served_as}): "
                f"{self.catalog.error}"
            )
        container: discord.ui.Container[LLMModelPanelView] = discord.ui.Container(
            discord.ui.TextDisplay("\n".join(lines))
        )

        models = self.catalog.models[:MAX_SELECT_OPTIONS]
        select_row: discord.ui.ActionRow[LLMModelPanelView] = discord.ui.ActionRow()
        select: discord.ui.Select[LLMModelPanelView] = discord.ui.Select(
            placeholder="Select a model" if models else "No models available",
            options=[
                discord.SelectOption(
                    label=model[:100], value=model, default=model == self.settings.llm_model
                )
                for model in models
            ]
            or [discord.SelectOption(label="(none)", value="__none__")],
            disabled=not models,
        )
        cast(Any, select).callback = self._make_select_callback(select)
        select_row.add_item(select)
        container.add_item(select_row)

        refresh_row: discord.ui.ActionRow[LLMModelPanelView] = discord.ui.ActionRow()
        refresh_row.add_item(self._refresh_button())
        container.add_item(refresh_row)

        self.add_item(container)

    def _make_select_callback(self, select: discord.ui.Select[LLMModelPanelView]) -> Any:
        async def on_select(interaction: discord.Interaction) -> None:
            model = select.values[0]
            corridor = _get_corridor(interaction)
            await corridor.set_llm_model(model)
            await _refresh(interaction, self.owner_id, confirmation=f"LLM model set to `{model}`.")

        return on_select

    def _refresh_button(self) -> discord.ui.Button[LLMModelPanelView]:
        button: discord.ui.Button[LLMModelPanelView] = discord.ui.Button(
            label="Refresh model list from LiteLLM", style=discord.ButtonStyle.secondary
        )

        async def callback(interaction: discord.Interaction) -> None:
            await _refresh(interaction, self.owner_id, force_refresh=True)

        cast(Any, button).callback = callback
        return button


__all__ = ["LLMModelPanelView", "MAX_SELECT_OPTIONS"]
