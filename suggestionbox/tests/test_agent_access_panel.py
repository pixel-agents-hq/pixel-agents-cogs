"""AgentAccessView -- the per-agent MCP access toggle panel. Exercises
`.create()` against a real Suggestionbox cog + FakeCorridor, and the
toggle button's callback end to end, same scope corridor's/toolbox's own
Components V2 panel tests use."""

from __future__ import annotations

import types
import unittest
from typing import Any, cast

import discord

from ..adapters.agent_access_panel import AgentAccessView
from ..suggestionbox import Suggestionbox
from .conftest import FakeBot, FakeCorridor


class TestAgentAccessView(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.bot = FakeBot(FakeCorridor(agent_keys=("architect", "agent2")))
        self.cog = Suggestionbox(bot=self.bot)
        await self.cog.cog_load()
        self.addAsyncCleanup(self.cog.cog_unload)
        self.bot.suggestionbox_cog = self.cog  # normally set by FakeBot.add_cog

    async def test_create_reads_agent_keys_from_corridor_and_defaults_to_disabled(self) -> None:
        view = await AgentAccessView.create(self.cog, owner_id=1)

        self.assertEqual(view.agent_keys, ["architect", "agent2"])
        self.assertEqual(view.enabled, {"architect": False, "agent2": False})

    async def test_create_reflects_a_previously_enabled_agent(self) -> None:
        await self.cog._repository.set_agent_enabled("architect", True)

        view = await AgentAccessView.create(self.cog, owner_id=1)

        self.assertTrue(view.enabled["architect"])

    async def test_toggle_button_flips_the_repository_and_rerenders(self) -> None:
        view = await AgentAccessView.create(self.cog, owner_id=1)
        button = view._toggle_button("architect")
        interaction = discord.Interaction(client=self.bot, user=types.SimpleNamespace(id=1))

        await cast(Any, button).callback(interaction)

        self.assertTrue(await self.cog._repository.is_agent_enabled("architect"))
        refreshed = interaction.last_edited_view
        self.assertIsInstance(refreshed, AgentAccessView)
        self.assertTrue(cast(AgentAccessView, refreshed).enabled["architect"])
        self.assertFalse(cast(AgentAccessView, refreshed).enabled["agent2"])

    async def test_interaction_check_rejects_a_non_owner(self) -> None:
        view = await AgentAccessView.create(self.cog, owner_id=1)
        interaction = discord.Interaction(client=self.bot, user=types.SimpleNamespace(id=2))

        allowed = await view.interaction_check(interaction)

        self.assertFalse(allowed)

    async def test_pagination_slices_agents_across_pages(self) -> None:
        # header TextDisplay + nav ActionRow + 2 items (TextDisplay,
        # ActionRow) per agent shown on that page.
        many_agents = tuple(f"agent{i}" for i in range(7))
        bot = FakeBot(FakeCorridor(agent_keys=many_agents))
        cog = Suggestionbox(bot=bot)
        await cog.cog_load()
        self.addAsyncCleanup(cog.cog_unload)

        first_page = await AgentAccessView.create(cog, owner_id=1, page_index=0)
        second_page = await AgentAccessView.create(cog, owner_id=1, page_index=1)

        self.assertEqual(first_page._page_count(), 2)
        self.assertEqual(len(cast(Any, first_page.children[0]).children), 2 + 5 * 2)
        self.assertEqual(len(cast(Any, second_page.children[0]).children), 2 + 2 * 2)


if __name__ == "__main__":
    unittest.main()
