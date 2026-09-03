"""Discord Components V2 panel: a read-only, paginated listing of every
custom agent bootcamp hosts and its full per-agent configuration.

Structurally cloned from telephonepole's own `AgentAccessView`
(`telephonepole/adapters/agent_access_panel.py`) -- same `Container` +
one `TextDisplay` row per entry + Prev/Next `ActionRow` shape -- but with
no per-row controls: this panel only ever displays, it never mutates
anything, so there is nothing to toggle.
"""

from __future__ import annotations

from typing import Any, cast

import discord

from ..domain import CustomAgent

PAGE_SIZE = 5


def _format_timeout(request_timeout_seconds: float | None) -> str:
    if request_timeout_seconds is None:
        return "default"
    return f"{request_timeout_seconds:g}s"


class AgentListView(discord.ui.LayoutView):  # type: ignore[misc, unused-ignore]
    """`agents` is the full, already-computed list of custom agents --
    pagination here is purely a local slice, no re-fetch on prev/next,
    same convention `AgentAccessView` itself uses."""

    def __init__(
        self,
        agents: list[CustomAgent],
        page_index: int,
        owner_id: int,
    ) -> None:
        super().__init__(timeout=180)
        self.agents = agents
        self.page_index = page_index
        self.owner_id = owner_id
        self._build()

    @classmethod
    async def create(cls, cog: Any, *, owner_id: int, page_index: int = 0) -> AgentListView:
        agents = list(await cast(Any, cog)._service.list_agents())
        return cls(agents, page_index, owner_id)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "Only the person who opened this panel can use these controls.", ephemeral=True
            )
            return False
        return True

    def _page_count(self) -> int:
        return max(1, -(-len(self.agents) // PAGE_SIZE))

    def _build(self) -> None:
        page = self.agents[self.page_index * PAGE_SIZE : (self.page_index + 1) * PAGE_SIZE]
        container: discord.ui.Container[AgentListView] = discord.ui.Container(
            discord.ui.TextDisplay(
                f"**Custom agents** — {len(self.agents)} agent(s) · "
                f"page {self.page_index + 1}/{self._page_count()}"
            )
        )

        if not page:
            container.add_item(discord.ui.TextDisplay("No custom agents exist yet."))
        for agent in page:
            container.add_item(discord.ui.TextDisplay(self._row_text(agent)))

        nav_row: discord.ui.ActionRow[AgentListView] = discord.ui.ActionRow()
        previous: discord.ui.Button[AgentListView] = discord.ui.Button(
            label="◀ Prev", style=discord.ButtonStyle.secondary, disabled=self.page_index == 0
        )
        cast(Any, previous).callback = self._on_prev
        following: discord.ui.Button[AgentListView] = discord.ui.Button(
            label="Next ▶",
            style=discord.ButtonStyle.secondary,
            disabled=self.page_index + 1 >= self._page_count(),
        )
        cast(Any, following).callback = self._on_next
        nav_row.add_item(previous)
        nav_row.add_item(following)
        container.add_item(nav_row)

        self.add_item(container)

    @staticmethod
    def _row_text(agent: CustomAgent) -> str:
        description = agent.description or "(auto -- system prompt preview)"
        return (
            f"**{agent.agent_key}**\n"
            f"permission: `{agent.permission_group}` · "
            f"max_tool_calls: `{agent.max_tool_calls}` · "
            f"debug_logging: `{agent.debug_logging}` · "
            f"request_timeout: `{_format_timeout(agent.request_timeout_seconds)}`\n"
            f"description: {description}"
        )

    async def _on_prev(self, interaction: discord.Interaction) -> None:
        if self.page_index > 0:
            await self._rerender(interaction, self.page_index - 1)

    async def _on_next(self, interaction: discord.Interaction) -> None:
        if self.page_index + 1 < self._page_count():
            await self._rerender(interaction, self.page_index + 1)

    async def _rerender(self, interaction: discord.Interaction, page_index: int) -> None:
        """Re-renders from the already-computed `agents` list -- never
        re-fetches `BootcampService.list_agents()` mid-pagination, same
        convention `AgentAccessView._rerender` already documents."""

        view = AgentListView(self.agents, page_index, self.owner_id)
        if interaction.response.is_done():
            await interaction.edit_original_response(view=view)
        else:
            await interaction.response.edit_message(view=view)


__all__ = ["AgentListView"]
