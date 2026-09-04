"""Discord Components V2 panel: the bot owner toggles, per agent registered
in corridor's `AgentDirectoryService`, whether that agent may use one
third-party MCP server's tools.

Structurally cloned from suggestionbox's own `AgentAccessView`
(`suggestionbox/adapters/agent_access_panel.py`), parameterized by
`server_name` since telephonepole gates access per registered server, not
globally the way suggestionbox (which only ever registers its own one
server) does.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

import discord

if TYPE_CHECKING:
    from redbot.core.bot import Red

    from .cog_base import CogBase

PAGE_SIZE = 5


def _get_telephonepole(interaction: discord.Interaction) -> CogBase:
    bot = cast("Red", interaction.client)
    cog = bot.get_cog("Telephonepole")
    if cog is None:
        raise RuntimeError("Telephonepole is not loaded.")
    return cast("CogBase", cog)


class AgentAccessView(discord.ui.LayoutView):  # type: ignore[misc, unused-ignore]
    """`agent_keys` is the full, already-computed list of currently
    registered agent keys -- pagination here is purely a local slice, no
    re-fetch on prev/next, same convention suggestionbox's own
    `AgentAccessView` uses."""

    def __init__(
        self,
        server_name: str,
        agent_keys: list[str],
        page_index: int,
        owner_id: int,
        enabled: dict[str, bool],
    ) -> None:
        super().__init__(timeout=180)
        self.server_name = server_name
        self.agent_keys = agent_keys
        self.page_index = page_index
        self.owner_id = owner_id
        # agent_key -> currently enabled. An agent absent here defaults to
        # disabled -- same "off by default" rule
        # RedTelephonepoleRepository.is_agent_enabled itself applies.
        self.enabled = enabled
        self._build()

    @classmethod
    async def create(
        cls, cog: Any, *, server_name: str, owner_id: int, page_index: int = 0
    ) -> AgentAccessView:
        agent_keys = [agent.agent_key for agent in cog.list_agents()]
        enabled = {
            key: await cog._repository.is_agent_enabled(server_name, key) for key in agent_keys
        }
        return cls(server_name, agent_keys, page_index, owner_id, enabled)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "Only the person who opened this panel can use these controls.", ephemeral=True
            )
            return False
        return True

    def _page_count(self) -> int:
        return max(1, -(-len(self.agent_keys) // PAGE_SIZE))

    def _build(self) -> None:
        page = self.agent_keys[self.page_index * PAGE_SIZE : (self.page_index + 1) * PAGE_SIZE]
        container: discord.ui.Container[AgentAccessView] = discord.ui.Container(
            discord.ui.TextDisplay(
                f"**MCP tool access for `{self.server_name}`** — {len(self.agent_keys)} agent(s) "
                f"· page {self.page_index + 1}/{self._page_count()}"
            )
        )

        for agent_key in page:
            container.add_item(discord.ui.TextDisplay(self._row_text(agent_key)))
            row: discord.ui.ActionRow[AgentAccessView] = discord.ui.ActionRow()
            row.add_item(self._toggle_button(agent_key))
            container.add_item(row)

        nav_row: discord.ui.ActionRow[AgentAccessView] = discord.ui.ActionRow()
        previous: discord.ui.Button[AgentAccessView] = discord.ui.Button(
            label="◀ Prev", style=discord.ButtonStyle.secondary, disabled=self.page_index == 0
        )
        cast(Any, previous).callback = self._on_prev
        following: discord.ui.Button[AgentAccessView] = discord.ui.Button(
            label="Next ▶",
            style=discord.ButtonStyle.secondary,
            disabled=self.page_index + 1 >= self._page_count(),
        )
        cast(Any, following).callback = self._on_next
        nav_row.add_item(previous)
        nav_row.add_item(following)
        container.add_item(nav_row)

        self.add_item(container)

    def _row_text(self, agent_key: str) -> str:
        enabled = self.enabled.get(agent_key, False)
        return f"**{agent_key}**\nMay use `{self.server_name}`'s MCP tools: **{'yes' if enabled else 'no'}**"

    def _toggle_button(self, agent_key: str) -> discord.ui.Button[AgentAccessView]:
        enabled = self.enabled.get(agent_key, False)
        button: discord.ui.Button[AgentAccessView] = discord.ui.Button(
            label="Disable" if enabled else "Enable",
            style=discord.ButtonStyle.danger if enabled else discord.ButtonStyle.primary,
        )

        async def callback(interaction: discord.Interaction) -> None:
            cog = _get_telephonepole(interaction)
            new_value = not enabled
            await cast(Any, cog)._repository.set_agent_enabled(
                self.server_name, agent_key, new_value
            )
            updated = dict(self.enabled)
            updated[agent_key] = new_value
            await self._rerender(interaction, updated, self.page_index)

        cast(Any, button).callback = callback
        return button

    async def _on_prev(self, interaction: discord.Interaction) -> None:
        if self.page_index > 0:
            await self._rerender(interaction, self.enabled, self.page_index - 1)

    async def _on_next(self, interaction: discord.Interaction) -> None:
        if self.page_index + 1 < self._page_count():
            await self._rerender(interaction, self.enabled, self.page_index + 1)

    async def _rerender(
        self, interaction: discord.Interaction, enabled: dict[str, bool], page_index: int
    ) -> None:
        """Re-render from the already-computed `agent_keys` and (possibly
        just-mutated) `enabled` -- never re-fetches `corridor.list_agents()`
        mid-pagination, same convention suggestionbox's own
        `AgentAccessView._rerender` already documents."""

        view = AgentAccessView(
            self.server_name, self.agent_keys, page_index, self.owner_id, enabled
        )
        if interaction.response.is_done():
            await interaction.edit_original_response(view=view)
        else:
            await interaction.response.edit_message(view=view)


__all__ = ["AgentAccessView"]
