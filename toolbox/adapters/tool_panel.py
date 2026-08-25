"""Components V2 panel: the bot owner turns any `[p]help`-listed command
into an LLM tool, per docs/toolbox-command-tool-toggle-design.md.

Two views:

- `ToolSelectionView` (bot-owner, global): select/deselect a candidate
  command and toggle each registered tool's global enabled default.
- `ToolGuildOverrideView` (guild admin, per-guild): override a currently
  registered tool's visibility for one guild only.

Both paginate `PAGE_SIZE` rows per page and rebuild themselves in place on
prev/next -- structurally closer to floorplan's `LayoutBrowseView` (page
state lives on the view instance) than corridor's settings_ui.py builder-
function style, since there's no live query to re-fetch per page here, just
a full in-memory list computed once when the panel is opened.
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, Any, cast

import discord

from .tool_candidates import CandidateCommand

if TYPE_CHECKING:
    from redbot.core.bot import Red

    from .cog_base import CogBase

PAGE_SIZE = 5


def _get_toolbox(interaction: discord.Interaction) -> CogBase:
    bot = cast("Red", interaction.client)
    toolbox = bot.get_cog("Toolbox")
    if toolbox is None:
        raise RuntimeError("Toolbox is not loaded.")
    return cast("CogBase", toolbox)


class ToolSelectionView(discord.ui.LayoutView):  # type: ignore[misc, unused-ignore]
    """The global panel: `candidates` is the full, already-computed list
    (see `tool_candidates.list_candidate_commands`) -- pagination here is
    purely a local slice, no re-fetch on prev/next."""

    def __init__(
        self,
        candidates: list[CandidateCommand],
        page_index: int,
        owner_id: int,
        enabled_defaults: dict[str, bool],
    ) -> None:
        super().__init__(timeout=180)
        self.candidates = candidates
        self.page_index = page_index
        self.owner_id = owner_id
        # Tool name -> explicit global default, from ToolVisibilityService
        # .all_defaults(). A tool absent here is enabled (is_enabled's own
        # "no explicit default means visible" rule) -- look it up with
        # .get(name, True), never a bare [name].
        self.enabled_defaults = enabled_defaults
        self._build()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "Only the person who opened this panel can use these controls.", ephemeral=True
            )
            return False
        return True

    def _page_count(self) -> int:
        return max(1, -(-len(self.candidates) // PAGE_SIZE))

    def _build(self) -> None:
        page = self.candidates[self.page_index * PAGE_SIZE : (self.page_index + 1) * PAGE_SIZE]
        container: discord.ui.Container[ToolSelectionView] = discord.ui.Container(
            discord.ui.TextDisplay(
                f"**LLM tool candidates** — {len(self.candidates)} command(s) · "
                f"page {self.page_index + 1}/{self._page_count()}"
            )
        )

        for candidate in page:
            container.add_item(discord.ui.TextDisplay(self._row_text(candidate)))
            row: discord.ui.ActionRow[ToolSelectionView] = discord.ui.ActionRow()
            if not candidate.already_decorated:
                row.add_item(self._selection_button(candidate))
            if candidate.already_decorated or candidate.selected:
                row.add_item(self._enabled_button(candidate))
            container.add_item(row)

        nav_row: discord.ui.ActionRow[ToolSelectionView] = discord.ui.ActionRow()
        previous: discord.ui.Button[ToolSelectionView] = discord.ui.Button(
            label="◀ Prev", style=discord.ButtonStyle.secondary, disabled=self.page_index == 0
        )
        cast(Any, previous).callback = self._on_prev
        following: discord.ui.Button[ToolSelectionView] = discord.ui.Button(
            label="Next ▶",
            style=discord.ButtonStyle.secondary,
            disabled=self.page_index + 1 >= self._page_count(),
        )
        cast(Any, following).callback = self._on_next
        nav_row.add_item(previous)
        nav_row.add_item(following)
        container.add_item(nav_row)

        self.add_item(container)

    def _row_text(self, candidate: CandidateCommand) -> str:
        if candidate.already_decorated:
            status = "Already an LLM tool (registered by its own cog)"
        elif candidate.selected:
            status = "Selected — wrapped as an LLM tool"
        else:
            status = "Not selected"
        lines = [
            f"**{candidate.qualified_name}** · `{candidate.tool_name}`",
            candidate.short_doc,
            status,
        ]
        if candidate.already_decorated or candidate.selected:
            enabled = self.enabled_defaults.get(candidate.tool_name, True)
            lines.append(f"Enabled by default: **{'yes' if enabled else 'no'}**")
        return "\n".join(lines)

    def _selection_button(
        self, candidate: CandidateCommand
    ) -> discord.ui.Button[ToolSelectionView]:
        button: discord.ui.Button[ToolSelectionView] = discord.ui.Button(
            label="Deselect" if candidate.selected else "Select",
            style=discord.ButtonStyle.danger if candidate.selected else discord.ButtonStyle.primary,
        )

        async def callback(interaction: discord.Interaction) -> None:
            toolbox = _get_toolbox(interaction)
            if candidate.selected:
                await toolbox.deselect_tool(candidate.qualified_name)
                updated = replace(candidate, selected=False)
            else:
                try:
                    await toolbox.select_tool(candidate.qualified_name)
                except ValueError as exc:
                    await interaction.response.send_message(str(exc), ephemeral=True)
                    return
                updated = replace(candidate, selected=True)
            await self._rerender(
                interaction, self._with_candidate(updated), self.enabled_defaults, self.page_index
            )

        cast(Any, button).callback = callback
        return button

    def _enabled_button(self, candidate: CandidateCommand) -> discord.ui.Button[ToolSelectionView]:
        currently_enabled = self.enabled_defaults.get(candidate.tool_name, True)
        button: discord.ui.Button[ToolSelectionView] = discord.ui.Button(
            label="Disable by default" if currently_enabled else "Enable by default",
            style=discord.ButtonStyle.danger if currently_enabled else discord.ButtonStyle.primary,
        )

        async def callback(interaction: discord.Interaction) -> None:
            toolbox = _get_toolbox(interaction)
            new_value = not currently_enabled
            await toolbox._tool_visibility_service.set_default(candidate.tool_name, new_value)
            updated_defaults = dict(self.enabled_defaults)
            updated_defaults[candidate.tool_name] = new_value
            await self._rerender(interaction, self.candidates, updated_defaults, self.page_index)

        cast(Any, button).callback = callback
        return button

    def _with_candidate(self, updated: CandidateCommand) -> list[CandidateCommand]:
        return [
            updated if c.qualified_name == updated.qualified_name else c for c in self.candidates
        ]

    async def _on_prev(self, interaction: discord.Interaction) -> None:
        if self.page_index > 0:
            await self._rerender(
                interaction, self.candidates, self.enabled_defaults, self.page_index - 1
            )

    async def _on_next(self, interaction: discord.Interaction) -> None:
        if self.page_index + 1 < self._page_count():
            await self._rerender(
                interaction, self.candidates, self.enabled_defaults, self.page_index + 1
            )

    async def _rerender(
        self,
        interaction: discord.Interaction,
        candidates: list[CandidateCommand],
        enabled_defaults: dict[str, bool],
        page_index: int,
    ) -> None:
        """Re-render from already-computed `candidates`/`enabled_defaults`
        -- never re-walks `bot.walk_commands()` or re-fetches Config. Both
        were computed once, at `[p]toolbox tools` invocation time (against
        a real `commands.Context` -- `can_run` needs one, and the
        interaction driving a button click is not one; passing it here
        previously made every `can_run` check raise and silently emptied
        the candidate list on the very first pagination click). A mutation
        (select/deselect/toggle) patches its own local copy of whichever of
        the two changed before calling this, rather than re-fetching
        either."""

        view = ToolSelectionView(candidates, page_index, self.owner_id, enabled_defaults)
        if interaction.response.is_done():
            await interaction.edit_original_response(view=view)
        else:
            await interaction.response.edit_message(view=view)


class ToolGuildOverrideView(discord.ui.LayoutView):  # type: ignore[misc, unused-ignore]
    """The per-guild panel: `tool_names` is every currently-registered
    tool's name (`corridor.list_tools()`), not the candidate list above --
    a guild admin overrides visibility for tools that exist, not commands
    nobody selected yet."""

    def __init__(
        self,
        tool_names: list[str],
        page_index: int,
        guild_id: int,
        admin_id: int,
        defaults: dict[str, bool],
        overrides: dict[str, bool],
    ) -> None:
        super().__init__(timeout=180)
        self.tool_names = tool_names
        self.page_index = page_index
        self.guild_id = guild_id
        self.admin_id = admin_id
        # Global defaults (ToolVisibilityService.all_defaults()) and this
        # guild's explicit overrides (.all_overrides(guild_id)) -- a tool
        # absent from `overrides` follows `defaults` (absent there too
        # means enabled); present in `overrides` means it has an explicit
        # per-guild value regardless of the default.
        self.defaults = defaults
        self.overrides = overrides
        self._build()

    def _effective(self, tool_name: str) -> bool:
        if tool_name in self.overrides:
            return self.overrides[tool_name]
        return self.defaults.get(tool_name, True)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.admin_id:
            await interaction.response.send_message(
                "Only the person who opened this panel can use these controls.", ephemeral=True
            )
            return False
        return True

    def _page_count(self) -> int:
        return max(1, -(-len(self.tool_names) // PAGE_SIZE))

    def _build(self) -> None:
        page = self.tool_names[self.page_index * PAGE_SIZE : (self.page_index + 1) * PAGE_SIZE]
        container: discord.ui.Container[ToolGuildOverrideView] = discord.ui.Container(
            discord.ui.TextDisplay(
                f"**LLM tool visibility for this server** — {len(self.tool_names)} tool(s) · "
                f"page {self.page_index + 1}/{self._page_count()}"
            )
        )

        for tool_name in page:
            container.add_item(discord.ui.TextDisplay(self._row_text(tool_name)))
            row: discord.ui.ActionRow[ToolGuildOverrideView] = discord.ui.ActionRow()
            row.add_item(self._toggle_button(tool_name))
            row.add_item(self._reset_button(tool_name))
            container.add_item(row)

        nav_row: discord.ui.ActionRow[ToolGuildOverrideView] = discord.ui.ActionRow()
        previous: discord.ui.Button[ToolGuildOverrideView] = discord.ui.Button(
            label="◀ Prev", style=discord.ButtonStyle.secondary, disabled=self.page_index == 0
        )
        cast(Any, previous).callback = self._on_prev
        following: discord.ui.Button[ToolGuildOverrideView] = discord.ui.Button(
            label="Next ▶",
            style=discord.ButtonStyle.secondary,
            disabled=self.page_index + 1 >= self._page_count(),
        )
        cast(Any, following).callback = self._on_next
        nav_row.add_item(previous)
        nav_row.add_item(following)
        container.add_item(nav_row)

        self.add_item(container)

    def _row_text(self, tool_name: str) -> str:
        effective = self._effective(tool_name)
        if tool_name in self.overrides:
            source = "explicit override for this server"
        else:
            source = "following the global default"
        return f"`{tool_name}`\nEnabled here: **{'yes' if effective else 'no'}** ({source})"

    def _toggle_button(self, tool_name: str) -> discord.ui.Button[ToolGuildOverrideView]:
        effective = self._effective(tool_name)
        button: discord.ui.Button[ToolGuildOverrideView] = discord.ui.Button(
            label="Disable here" if effective else "Enable here",
            style=discord.ButtonStyle.danger if effective else discord.ButtonStyle.primary,
        )

        async def callback(interaction: discord.Interaction) -> None:
            toolbox = _get_toolbox(interaction)
            new_value = not effective
            await toolbox._tool_visibility_service.set_override(self.guild_id, tool_name, new_value)
            updated_overrides = dict(self.overrides)
            updated_overrides[tool_name] = new_value
            await self._rerender(interaction, updated_overrides, self.page_index)

        cast(Any, button).callback = callback
        return button

    def _reset_button(self, tool_name: str) -> discord.ui.Button[ToolGuildOverrideView]:
        has_override = tool_name in self.overrides
        button: discord.ui.Button[ToolGuildOverrideView] = discord.ui.Button(
            label="Reset to default", style=discord.ButtonStyle.secondary, disabled=not has_override
        )

        async def callback(interaction: discord.Interaction) -> None:
            toolbox = _get_toolbox(interaction)
            await toolbox._tool_visibility_service.clear_override(self.guild_id, tool_name)
            updated_overrides = dict(self.overrides)
            updated_overrides.pop(tool_name, None)
            await self._rerender(interaction, updated_overrides, self.page_index)

        cast(Any, button).callback = callback
        return button

    async def _on_prev(self, interaction: discord.Interaction) -> None:
        if self.page_index > 0:
            await self._rerender(interaction, self.overrides, self.page_index - 1)

    async def _on_next(self, interaction: discord.Interaction) -> None:
        if self.page_index + 1 < self._page_count():
            await self._rerender(interaction, self.overrides, self.page_index + 1)

    async def _rerender(
        self,
        interaction: discord.Interaction,
        overrides: dict[str, bool],
        page_index: int,
    ) -> None:
        """Re-render from the already-computed `tool_names`/`defaults` and
        (possibly just-mutated) `overrides` -- never re-fetches
        corridor.list_tools() or Config, for the same reason
        ToolSelectionView._rerender doesn't: those were read once, when
        the panel was opened, and nothing here needs a fresher view of
        them mid-pagination."""

        view = ToolGuildOverrideView(
            self.tool_names, page_index, self.guild_id, self.admin_id, self.defaults, overrides
        )
        if interaction.response.is_done():
            await interaction.edit_original_response(view=view)
        else:
            await interaction.response.edit_message(view=view)


__all__ = ["ToolGuildOverrideView", "ToolSelectionView"]
