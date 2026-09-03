"""The Components V2 create flow: `[p]bootcamp create` sends
`CreateAgentPromptView`; its button opens `CreateAgentModal`; a
successful submission creates the agent and follows up with
`AgentAccessConfigView` for choosing who may use it.

Uses corridor's shared `discord.Interaction`/`discord.ui.Modal` stubs
(installed by `../conftest.py`) the same way `corridor/tests/
test_settings_ui.py` does -- real `modal.on_submit(interaction)` calls
against a fake interaction, not hand-rolled mocks.
"""

from __future__ import annotations

import unittest

import discord

from corridor.testing import followup_messages, refreshed_view, shown_modal

from ..adapters.create_agent_panel import (
    AgentAccessConfigView,
    CreateAgentModal,
    CreateAgentPromptView,
)
from ..bootcamp import Bootcamp
from .conftest import FakeBot, FakeContext, create_agent


def _fill(
    modal: CreateAgentModal,
    *,
    agent_key: str = "recruiter",
    system_prompt: str = "You screen job applicants.",
    description: str = "",
    max_tool_calls: str = "8",
    request_timeout: str = "default",
) -> None:
    modal.agent_key_input.value = agent_key
    modal.system_prompt_input.value = system_prompt
    modal.description_input.value = description
    modal.max_tool_calls_input.value = max_tool_calls
    modal.request_timeout_input.value = request_timeout


class TestCreateAgentPromptView(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.bot = FakeBot()
        self.cog = Bootcamp(bot=self.bot)
        await self.cog.cog_load()
        self.ctx = FakeContext()

    async def test_the_button_opens_the_create_agent_modal(self) -> None:
        view = CreateAgentPromptView(self.cog, owner_id=self.ctx.author.id)
        container = view.children[0]
        button = container.children[1]
        interaction = discord.Interaction(
            guild=self.ctx.guild, user=self.ctx.author, client=self.bot
        )

        await button.callback(interaction)

        modal = shown_modal(interaction)
        self.assertIsInstance(modal, CreateAgentModal)

    async def test_interaction_check_blocks_a_different_member(self) -> None:
        view = CreateAgentPromptView(self.cog, owner_id=self.ctx.author.id)
        other_member = type("M", (), {"id": 999})()
        interaction = discord.Interaction(guild=self.ctx.guild, user=other_member, client=self.bot)

        allowed = await view.interaction_check(interaction)

        self.assertFalse(allowed)
        self.assertIsNone(shown_modal(interaction))


class TestCreateAgentModal(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.bot = FakeBot()
        self.cog = Bootcamp(bot=self.bot)
        await self.cog.cog_load()
        self.ctx = FakeContext()
        self.modal = CreateAgentModal(self.cog)

    def _interaction(self) -> discord.Interaction:
        return discord.Interaction(guild=self.ctx.guild, user=self.ctx.author, client=self.bot)

    async def test_creates_the_agent_and_follows_up_with_the_access_config_panel(self) -> None:
        _fill(
            self.modal,
            description="Consult for anything about screening applicants.",
            max_tool_calls="5",
            request_timeout="45",
        )
        interaction = self._interaction()

        await self.modal.on_submit(interaction)

        agent = await self.cog._service.get_agent("recruiter")  # type: ignore[union-attr]
        assert agent is not None
        self.assertEqual(agent.description, "Consult for anything about screening applicants.")
        self.assertEqual(agent.max_tool_calls, 5)
        self.assertEqual(agent.request_timeout_seconds, 45.0)
        [(_args, kwargs)] = interaction.followup.sent
        self.assertIsInstance(kwargs["view"], AgentAccessConfigView)
        self.assertEqual(kwargs["view"].agent.agent_key, "recruiter")

    async def test_blank_description_is_stored_as_none(self) -> None:
        _fill(self.modal, description="   ")

        await self.modal.on_submit(self._interaction())

        agent = await self.cog._service.get_agent("recruiter")  # type: ignore[union-attr]
        assert agent is not None
        self.assertIsNone(agent.description)

    async def test_default_max_tool_calls_and_timeout_are_used_unedited(self) -> None:
        _fill(self.modal)

        await self.modal.on_submit(self._interaction())

        agent = await self.cog._service.get_agent("recruiter")  # type: ignore[union-attr]
        assert agent is not None
        self.assertEqual(agent.max_tool_calls, 8)
        self.assertIsNone(agent.request_timeout_seconds)

    async def test_an_invalid_max_tool_calls_is_rejected_without_creating_anything(self) -> None:
        _fill(self.modal, max_tool_calls="not a number")
        interaction = self._interaction()

        await self.modal.on_submit(interaction)

        self.assertIsNone(await self.cog._service.get_agent("recruiter"))  # type: ignore[union-attr]
        self.assertTrue(interaction.response.is_done())
        self.assertEqual(interaction.followup.sent, [])

    async def test_an_invalid_timeout_is_rejected_without_creating_anything(self) -> None:
        _fill(self.modal, request_timeout="not a number")

        await self.modal.on_submit(self._interaction())

        self.assertIsNone(await self.cog._service.get_agent("recruiter"))  # type: ignore[union-attr]

    async def test_a_service_level_validation_error_is_surfaced_without_creating_anything(
        self,
    ) -> None:
        _fill(self.modal, agent_key="Not Valid")

        await self.modal.on_submit(self._interaction())

        self.assertEqual(await self.cog._service.list_agents(), ())  # type: ignore[union-attr]

    async def test_a_reserved_agent_key_is_rejected(self) -> None:
        _fill(self.modal, agent_key="list")

        await self.modal.on_submit(self._interaction())

        self.assertIsNone(await self.cog._service.get_agent("list"))  # type: ignore[union-attr]


class TestAgentAccessConfigView(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.bot = FakeBot()
        self.cog = Bootcamp(bot=self.bot)
        await self.cog.cog_load()
        self.ctx = FakeContext()
        await create_agent(self.cog, "recruiter", "prompt")
        self.agent = await self.cog._service.get_agent("recruiter")  # type: ignore[union-attr]
        assert self.agent is not None

    def _interaction(self) -> discord.Interaction:
        return discord.Interaction(guild=self.ctx.guild, user=self.ctx.author, client=self.bot)

    async def test_create_lists_the_guilds_configured_permission_groups(self) -> None:
        self.bot.corridor.permission_groups = (
            type("G", (), {"key": "keyholder", "label": "Keyholder"})(),
        )

        view = await AgentAccessConfigView.create(
            self.cog, agent=self.agent, owner_id=self.ctx.author.id, guild_id=self.ctx.guild.id
        )

        select = view._permission_select()
        values = {option.value for option in select.options}
        self.assertEqual(values, {"employee", "owner", "keyholder"})

    async def test_permission_select_updates_the_agent_and_rerenders(self) -> None:
        view = AgentAccessConfigView(self.cog, self.agent, self.ctx.author.id, ())
        select = view._permission_select()
        select.values = ["keyholder"]
        interaction = self._interaction()

        await select.callback(interaction)

        updated = await self.cog._service.get_agent("recruiter")  # type: ignore[union-attr]
        assert updated is not None
        self.assertEqual(updated.permission_group, "keyholder")
        rerendered = refreshed_view(interaction)
        self.assertIsInstance(rerendered, AgentAccessConfigView)
        self.assertEqual(rerendered.agent.permission_group, "keyholder")

    async def test_debug_toggle_flips_the_agent_and_rerenders(self) -> None:
        view = AgentAccessConfigView(self.cog, self.agent, self.ctx.author.id, ())
        button = view._debug_toggle_button()
        interaction = self._interaction()

        await button.callback(interaction)

        updated = await self.cog._service.get_agent("recruiter")  # type: ignore[union-attr]
        assert updated is not None
        self.assertTrue(updated.debug_logging)
        rerendered = refreshed_view(interaction)
        self.assertIsInstance(rerendered, AgentAccessConfigView)
        self.assertTrue(rerendered.agent.debug_logging)

    async def test_interaction_check_blocks_a_different_member(self) -> None:
        view = AgentAccessConfigView(self.cog, self.agent, self.ctx.author.id, ())
        other_member = type("M", (), {"id": 999})()
        interaction = discord.Interaction(guild=self.ctx.guild, user=other_member, client=self.bot)

        allowed = await view.interaction_check(interaction)

        self.assertFalse(allowed)


class TestCreateAgentPanelFollowUpValidation(unittest.IsolatedAsyncioTestCase):
    """followup.send() (used to deliver AgentAccessConfigView) raises if
    called before the interaction was ever acknowledged -- a regression
    test that the create modal always acknowledges via
    `interaction.response.send_message` before its followup, never the
    other way around."""

    async def test_followup_after_response_never_raises(self) -> None:
        bot = FakeBot()
        cog = Bootcamp(bot=bot)
        await cog.cog_load()
        ctx = FakeContext()
        modal = CreateAgentModal(cog)
        _fill(modal)
        interaction = discord.Interaction(guild=ctx.guild, user=ctx.author, client=bot)

        await modal.on_submit(interaction)  # must not raise

        self.assertEqual(followup_messages(interaction), [None])  # view-only follow-up
        self.assertEqual(len(interaction.followup.sent), 1)
