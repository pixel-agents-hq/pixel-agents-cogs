"""Discord Components V2 flow for `[p]bootcamp create`:

1. `CreateAgentPromptView` -- sent by the command itself, one button.
2. Clicking it opens `CreateAgentModal` -- the five fields a modal can
   hold (Discord caps a `Modal` at 5 components): agent key, system
   prompt, description, max tool calls, request timeout.
3. On successful submission, `AgentAccessConfigView` is sent as a
   follow-up -- a permission-group select and a debug-logging toggle for
   the agent just created, since those two settings don't fit in the same
   modal and are better suited to a select/toggle than free text anyway
   (`permission_group` should be constrained to the guild's actually
   configured groups, not typed by hand).

Every control closes over `cog` directly (passed at construction), the
same convention `agent_list_panel.py`'s `AgentListView` already uses --
not `interaction.client.get_cog("Bootcamp")` (corridor's own
`settings_ui.py` resolves that way instead, but only because its modals
are built from module-level functions with no cog reference already in
scope; here every view/modal is already handed one by the command that
creates it).
"""

from __future__ import annotations

from typing import Any, cast

import discord

from ..application import MAX_DESCRIPTION_LENGTH
from ..domain import DEFAULT_MAX_TOOL_CALLS, CustomAgent
from .agent_list_panel import AgentListView
from .validation import parse_max_tool_calls, parse_request_timeout

_RESERVED_PERMISSION_OPTIONS: tuple[tuple[str, str], ...] = (
    ("employee", "Employee (everyone)"),
    ("owner", "Owner (bot owner only)"),
)


class CreateAgentModal(discord.ui.Modal):
    """The five fields `create_agent` needs that are free-form text --
    `permission_group`/`debug_logging` are set afterward, on
    `AgentAccessConfigView`, since a Select/toggle fits them better than a
    `TextInput`."""

    def __init__(self, cog: Any) -> None:
        super().__init__(title="Create custom agent")
        self._cog = cog
        self.agent_key_input: discord.ui.TextInput[CreateAgentModal] = discord.ui.TextInput(
            label="Agent key (lowercase, a-z 0-9 _)",
            placeholder="recruiter",
            required=True,
            max_length=50,
        )
        self.system_prompt_input: discord.ui.TextInput[CreateAgentModal] = discord.ui.TextInput(
            label="System prompt",
            style=discord.TextStyle.paragraph,
            required=True,
            max_length=4000,
        )
        self.description_input: discord.ui.TextInput[CreateAgentModal] = discord.ui.TextInput(
            label="Description (when should this be consulted?)",
            placeholder="Shown to pico's LLM as this agent's consult_<key> tool description.",
            style=discord.TextStyle.paragraph,
            required=False,
            max_length=MAX_DESCRIPTION_LENGTH,
        )
        self.max_tool_calls_input: discord.ui.TextInput[CreateAgentModal] = discord.ui.TextInput(
            label="Max tool calls",
            default=str(DEFAULT_MAX_TOOL_CALLS),
            required=True,
            max_length=4,
        )
        self.request_timeout_input: discord.ui.TextInput[CreateAgentModal] = discord.ui.TextInput(
            label="Request timeout, seconds (or `default`)",
            default="default",
            required=True,
            max_length=10,
        )
        for item in (
            self.agent_key_input,
            self.system_prompt_input,
            self.description_input,
            self.max_tool_calls_input,
            self.request_timeout_input,
        ):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        max_tool_calls, mtc_error = parse_max_tool_calls(self.max_tool_calls_input.value)
        if mtc_error is not None:
            await interaction.response.send_message(mtc_error, ephemeral=True)
            return
        timeout_value, timeout_error = parse_request_timeout(self.request_timeout_input.value)
        if timeout_error is not None:
            await interaction.response.send_message(timeout_error, ephemeral=True)
            return
        assert max_tool_calls is not None  # mtc_error is None <=> a value was parsed

        agent_key = self.agent_key_input.value.strip().lower()
        description = self.description_input.value.strip() or None
        service = cast(Any, self._cog)._service
        error = await service.create_agent(
            agent_key,
            self.system_prompt_input.value,
            description=description,
            max_tool_calls=max_tool_calls,
            request_timeout_seconds=timeout_value,
        )
        if error is not None:
            await interaction.response.send_message(
                f"Could not create agent: {error}", ephemeral=True
            )
            return

        await interaction.response.send_message(
            f"**{agent_key}** created -- reachable via pico's own `consult_{agent_key}` tool "
            f"and directly with `[p]bootcamp ask {agent_key} <prompt>`. Choose who may use it:"
        )
        agent = await service.get_agent(agent_key)
        assert agent is not None
        view = await AgentAccessConfigView.create(
            self._cog, agent=agent, owner_id=interaction.user.id, guild_id=interaction.guild.id
        )
        await interaction.followup.send(view=view)


class CreateAgentPromptView(discord.ui.LayoutView):
    """Sent by `[p]bootcamp create` itself -- a Discord `Modal` can only be
    opened in response to a real interaction (a slash-command invocation
    already is one, but a classic prefix invocation like `[p]bootcamp
    create` is not), so the command sends this one-button prompt and the
    button's own click is what opens `CreateAgentModal`."""

    def __init__(self, cog: Any, owner_id: int) -> None:
        super().__init__()
        self._cog = cog
        self.owner_id = owner_id
        self._build()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "Only the person who ran this command can use this button.", ephemeral=True
            )
            return False
        return True

    def _build(self) -> None:
        container: discord.ui.Container[CreateAgentPromptView] = discord.ui.Container(
            discord.ui.TextDisplay(
                "Create a custom LLM agent -- set its key, system prompt, description, "
                "tool-call budget, and LLM request timeout. Who may use it is chosen "
                "afterward."
            )
        )
        button: discord.ui.Button[CreateAgentPromptView] = discord.ui.Button(
            label="Create custom agent", style=discord.ButtonStyle.primary
        )
        cast(Any, button).callback = self._on_create
        # A Button (and a Select, see AgentAccessConfigView._build below)
        # is not itself a valid direct child of a Container -- Discord
        # rejects the message with "Invalid Form Body ... type must be
        # one of (1, 9, 10, 12, 13, 14)" (ActionRow/Section/TextDisplay/
        # MediaGallery/File/Separator) otherwise. It must be wrapped in an
        # ActionRow first, the same convention every control in corridor's
        # own settings_ui.py already follows.
        row: discord.ui.ActionRow[CreateAgentPromptView] = discord.ui.ActionRow()
        row.add_item(button)
        container.add_item(row)
        self.add_item(container)

    async def _on_create(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(CreateAgentModal(self._cog))


class AgentAccessConfigView(discord.ui.LayoutView):
    """Sent as a follow-up right after creation, and reachable no other
    way -- editing permission/debug logging later goes through
    `[p]bootcamp permission`/`debuglogging` directly, matching every other
    per-agent setting's own edit command."""

    def __init__(
        self,
        cog: Any,
        agent: CustomAgent,
        owner_id: int,
        guild_groups: tuple[Any, ...],
    ) -> None:
        super().__init__()
        self._cog = cog
        self.agent = agent
        self.owner_id = owner_id
        self._guild_groups = guild_groups
        self._build()

    @classmethod
    async def create(
        cls, cog: Any, *, agent: CustomAgent, owner_id: int, guild_id: int
    ) -> AgentAccessConfigView:
        corridor = cast(Any, cog)._corridor
        groups = await corridor.list_permission_groups(guild_id)
        return cls(cog, agent, owner_id, groups)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "Only the person who created this agent can use these controls.", ephemeral=True
            )
            return False
        return True

    def _build(self) -> None:
        container: discord.ui.Container[AgentAccessConfigView] = discord.ui.Container(
            discord.ui.TextDisplay(AgentListView._row_text(self.agent))
        )
        # Both the Select and the Button need their own ActionRow -- see
        # CreateAgentPromptView._build's comment on why a bare Select/Button
        # cannot be added to a Container directly.
        select_row: discord.ui.ActionRow[AgentAccessConfigView] = discord.ui.ActionRow()
        select_row.add_item(self._permission_select())
        container.add_item(select_row)
        button_row: discord.ui.ActionRow[AgentAccessConfigView] = discord.ui.ActionRow()
        button_row.add_item(self._debug_toggle_button())
        container.add_item(button_row)
        self.add_item(container)

    def _permission_select(self) -> discord.ui.Select[AgentAccessConfigView]:
        options = [
            discord.SelectOption(label=label, value=key, default=self.agent.permission_group == key)
            for key, label in _RESERVED_PERMISSION_OPTIONS
        ] + [
            discord.SelectOption(
                label=group.label,
                value=group.key,
                default=self.agent.permission_group == group.key,
            )
            for group in self._guild_groups
        ]
        select: discord.ui.Select[AgentAccessConfigView] = discord.ui.Select(
            placeholder="Who may use this agent", options=options
        )

        async def callback(interaction: discord.Interaction) -> None:
            error = await cast(Any, self._cog)._service.set_permission_group(
                self.agent.agent_key, select.values[0]
            )
            if error is not None:
                await interaction.response.send_message(error, ephemeral=True)
                return
            await self._rerender(interaction)

        select.callback = callback  # type: ignore[method-assign]
        return select

    def _debug_toggle_button(self) -> discord.ui.Button[AgentAccessConfigView]:
        button: discord.ui.Button[AgentAccessConfigView] = discord.ui.Button(
            label=f"Debug logging: {'on' if self.agent.debug_logging else 'off'}",
            style=discord.ButtonStyle.secondary,
        )

        async def callback(interaction: discord.Interaction) -> None:
            error = await cast(Any, self._cog)._service.set_debug_logging(
                self.agent.agent_key, not self.agent.debug_logging
            )
            if error is not None:
                await interaction.response.send_message(error, ephemeral=True)
                return
            await self._rerender(interaction)

        cast(Any, button).callback = callback
        return button

    async def _rerender(self, interaction: discord.Interaction) -> None:
        """Re-fetches the agent (unlike `AgentListView._rerender`, which
        deliberately reuses its already-computed snapshot -- this view
        mutates the one agent it shows, so re-rendering from stale local
        state would keep displaying the pre-toggle value)."""

        agent = await cast(Any, self._cog)._service.get_agent(self.agent.agent_key)
        assert agent is not None
        view = AgentAccessConfigView(self._cog, agent, self.owner_id, self._guild_groups)
        if interaction.response.is_done():
            await interaction.edit_original_response(view=view)
        else:
            await interaction.response.edit_message(view=view)


__all__ = ["AgentAccessConfigView", "CreateAgentModal", "CreateAgentPromptView"]
