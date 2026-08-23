"""Components V2 UI: a three-step flow that lets the bot owner publish any
corridor bus event, built generically from corridor's own event catalog
(application/event_catalog.py) -- adding a new event to corridor's domain
model needs zero changes here.

Step 1 (EventPickerView): a Select of event names.
Step 2 (EventDetailView), built per event from its field schema: a field
  typed AgentRef becomes a native UserSelect (the picked member supplies
  discord_user_id/is_bot directly; guild_id comes from the invoking guild);
  a Literal[...]-typed field becomes a Select with the literal's own values
  as options; a Publish button.
Step 3, only if scalar fields remain: a dynamically-built Modal with one
  TextInput per remaining field.

Mirrors corridor/adapters/settings_ui.py's View -> Modal idioms exactly
(_get_corridor via bot.get_cog, factory functions closing over a callback,
send_modal/edit_message, ephemeral followup confirmations)."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

import discord

from ..application import (
    FieldKind,
    build_event,
    classify,
    list_publishable_events,
    literal_options,
)
from ..domain import EventSpec, FieldSpec

if TYPE_CHECKING:
    from redbot.core.bot import Red

    # corridor's own CogBase (publish_event/send_reply/...), not
    # testbench's local one -- _get_corridor resolves the live Corridor
    # Cog, which is an instance of corridor's CogBase.
    from corridor.adapters.cog_base import CogBase as CorridorCogBase

# discord_user_id, guild_id, is_bot -- everything AgentRef needs, reduced
# from a picked discord.Member before reaching the framework-agnostic
# application layer (see application/event_builder.py's docstring).
AgentSelection = tuple[int, int, bool]


def _get_corridor(interaction: discord.Interaction) -> CorridorCogBase:
    # `interaction.client` is typed as `discord.Client`, which has no
    # `get_cog` -- that's a Red `Red`/`commands.Bot` method. Red is absent
    # from the lightweight CI type environment, so the cast below is the
    # typed boundary for that gap (mirrors settings_ui.py's `_get_corridor`).
    bot = cast("Red", interaction.client)
    corridor = bot.get_cog("Corridor")
    if corridor is None:
        raise RuntimeError("Corridor is not loaded.")
    return cast("CorridorCogBase", corridor)


def _agent_fields(spec: EventSpec) -> tuple[FieldSpec, ...]:
    return tuple(field for field in spec.fields if classify(field) is FieldKind.AGENT_REF)


def _literal_fields(spec: EventSpec) -> tuple[FieldSpec, ...]:
    return tuple(field for field in spec.fields if classify(field) is FieldKind.LITERAL)


def _scalar_fields(spec: EventSpec) -> tuple[FieldSpec, ...]:
    return tuple(field for field in spec.fields if classify(field) is FieldKind.SCALAR)


async def _respond(interaction: discord.Interaction, content: str) -> None:
    if interaction.response.is_done():
        await interaction.followup.send(content, ephemeral=True)
    else:
        await interaction.response.send_message(content, ephemeral=True)


async def _publish(
    interaction: discord.Interaction,
    spec: EventSpec,
    agent_selections: dict[str, AgentSelection],
    literal_selections: dict[str, str],
    scalar_inputs: dict[str, str],
) -> None:
    corridor = _get_corridor(interaction)
    try:
        event = build_event(
            spec,
            agent_selections=agent_selections,
            literal_selections=literal_selections,
            scalar_inputs=scalar_inputs,
        )
    except ValueError as exc:
        await _respond(interaction, f"Couldn't build `{spec.name}`: {exc}")
        return

    await corridor.publish_event(event)
    await _respond(interaction, f"Published `{spec.name}`: {event!r}")


class EventPickerView(discord.ui.LayoutView):
    def __init__(self, specs: tuple[EventSpec, ...] | None = None) -> None:
        super().__init__()
        specs = specs if specs is not None else list_publishable_events()
        container: discord.ui.Container[EventPickerView] = discord.ui.Container(
            discord.ui.TextDisplay(
                "**Publish a corridor bus event**\nPick an event type to fill in and publish."
            )
        )
        container.add_item(discord.ui.ActionRow(_event_select(specs)))
        self.add_item(container)


def _event_select(specs: tuple[EventSpec, ...]) -> discord.ui.Select[EventPickerView]:
    by_name = {spec.name: spec for spec in specs}
    select: discord.ui.Select[EventPickerView] = discord.ui.Select(
        placeholder="Event type",
        options=[discord.SelectOption(label=spec.name, value=spec.name) for spec in specs],
    )

    async def callback(interaction: discord.Interaction) -> None:
        chosen = by_name[select.values[0]]
        await interaction.response.edit_message(view=EventDetailView(chosen))

    select.callback = callback  # type: ignore[method-assign]
    return select


def _user_select(field: FieldSpec) -> discord.ui.UserSelect[EventDetailView]:
    select: discord.ui.UserSelect[EventDetailView] = discord.ui.UserSelect(
        placeholder=f"{field.name}: pick a Discord member", min_values=1, max_values=1
    )

    async def callback(interaction: discord.Interaction) -> None:
        # The pick is already captured on `select.values` -- this callback
        # only needs to acknowledge the interaction within Discord's 3s
        # window (an unassigned callback defaults to a no-op that never
        # responds, which surfaces to the user as "Pico didn't respond in
        # time"). Re-editing with the same view is a cheap, harmless ack.
        await interaction.response.edit_message(view=select.view)

    select.callback = callback  # type: ignore[method-assign]
    return select


def _literal_select(field: FieldSpec) -> discord.ui.Select[EventDetailView]:
    select: discord.ui.Select[EventDetailView] = discord.ui.Select(
        placeholder=field.name,
        min_values=1,
        max_values=1,
        options=[
            discord.SelectOption(label=value, value=value)
            for value in literal_options(field.type_str)
        ],
    )

    async def callback(interaction: discord.Interaction) -> None:
        await interaction.response.edit_message(view=select.view)

    select.callback = callback  # type: ignore[method-assign]
    return select


class EventDetailView(discord.ui.LayoutView):
    def __init__(self, spec: EventSpec) -> None:
        super().__init__()
        self._spec = spec
        self._agent_selects: dict[str, discord.ui.UserSelect[EventDetailView]] = {
            field.name: _user_select(field) for field in _agent_fields(spec)
        }
        self._literal_selects: dict[str, discord.ui.Select[EventDetailView]] = {
            field.name: _literal_select(field) for field in _literal_fields(spec)
        }

        container: discord.ui.Container[EventDetailView] = discord.ui.Container(
            discord.ui.TextDisplay(f"**{spec.name}**\nFill in the fields below, then Publish.")
        )
        for user_select in self._agent_selects.values():
            container.add_item(discord.ui.ActionRow(user_select))
        for literal_select in self._literal_selects.values():
            container.add_item(discord.ui.ActionRow(literal_select))
        container.add_item(discord.ui.ActionRow(self._publish_button()))
        self.add_item(container)

    def _publish_button(self) -> discord.ui.Button[EventDetailView]:
        button: discord.ui.Button[EventDetailView] = discord.ui.Button(
            label="Publish", style=discord.ButtonStyle.primary
        )

        async def callback(interaction: discord.Interaction) -> None:
            # Discord doesn't enforce "required" on View components the way
            # Modals enforce it on TextInput -- validate by hand before
            # proceeding.
            for field_name, user_select in self._agent_selects.items():
                if not user_select.values:
                    await _respond(interaction, f"Pick a Discord member for `{field_name}` first.")
                    return
            for field_name, literal_select in self._literal_selects.items():
                if not literal_select.values:
                    await _respond(interaction, f"Pick a value for `{field_name}` first.")
                    return

            assert interaction.guild is not None
            guild_id = interaction.guild.id
            agent_selections: dict[str, AgentSelection] = {
                field_name: (member.id, guild_id, member.bot)
                for field_name, user_select in self._agent_selects.items()
                for member in [user_select.values[0]]
            }
            literal_selections = {
                field_name: literal_select.values[0]
                for field_name, literal_select in self._literal_selects.items()
            }

            scalar_fields = _scalar_fields(self._spec)
            if scalar_fields:
                await interaction.response.send_modal(
                    _FieldsModal(self._spec, scalar_fields, agent_selections, literal_selections)
                )
                return

            await _publish(interaction, self._spec, agent_selections, literal_selections, {})

        button.callback = callback  # type: ignore[method-assign]
        return button


class _FieldsModal(discord.ui.Modal):
    def __init__(
        self,
        spec: EventSpec,
        scalar_fields: tuple[FieldSpec, ...],
        agent_selections: dict[str, AgentSelection],
        literal_selections: dict[str, str],
    ) -> None:
        super().__init__(title=spec.name[:45])
        self._spec = spec
        self._agent_selections = agent_selections
        self._literal_selections = literal_selections
        self._inputs: dict[str, discord.ui.TextInput[_FieldsModal]] = {}
        for field in scalar_fields:
            text_input: discord.ui.TextInput[_FieldsModal] = discord.ui.TextInput(
                label=field.name[:45], required=field.required, default="", max_length=4000
            )
            self._inputs[field.name] = text_input
            self.add_item(text_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        scalar_inputs = {name: text_input.value for name, text_input in self._inputs.items()}
        await _publish(
            interaction,
            self._spec,
            self._agent_selections,
            self._literal_selections,
            scalar_inputs,
        )
