"""Components V2 settings UI. Two entry points:

- `SharedSettingsView`: corridor's own `[p]corridor settings` panel.
- `build_shared_settings_container()`: the same controls as a mountable
  fragment any cog can append to its own LayoutView, so every generated
  cog's settings command shows "shared settings" + "this cog's own settings"
  in one panel without re-implementing these controls.

Both read/write through `bot.get_cog("Corridor")` resolved from the
interaction's client, so a mounted fragment is self-sufficient regardless of
which cog's view it lives in.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord

from ..domain import GuildSettings, IconPreference, IconSource, ReplyMode

if TYPE_CHECKING:
    from .cog_base import CogBase


def _get_corridor(interaction: discord.Interaction) -> CogBase:
    corridor = interaction.client.get_cog("Corridor")
    if corridor is None:
        raise RuntimeError("Corridor is not loaded.")
    return corridor  # type: ignore[return-value]


class SharedSettingsModal(discord.ui.Modal):
    def __init__(self, guild_settings: GuildSettings) -> None:
        super().__init__(title="Reply Settings")
        self.footer_input: discord.ui.TextInput[SharedSettingsModal] = discord.ui.TextInput(
            label="Footer text",
            required=False,
            default=guild_settings.reply.footer_text or "",
            max_length=200,
        )
        self.custom_icon_input: discord.ui.TextInput[SharedSettingsModal] = discord.ui.TextInput(
            label="Custom icon URL (used when icon source = custom)",
            required=False,
            default=guild_settings.reply.icon.custom_url or "",
            max_length=500,
        )
        self.add_item(self.footer_input)
        self.add_item(self.custom_icon_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        corridor = _get_corridor(interaction)
        footer_text = self.footer_input.value or None
        custom_url = self.custom_icon_input.value or None
        await corridor.set_footer_text(interaction.guild.id, footer_text)
        settings = await corridor.guild_settings(interaction.guild.id)
        await corridor.set_icon_preference(
            interaction.guild.id,
            IconPreference(source=settings.reply.icon.source, custom_url=custom_url),
        )
        await interaction.response.send_message("Reply settings updated.", ephemeral=True)


def build_shared_settings_container(
    guild_settings: GuildSettings,
) -> discord.ui.Container[discord.ui.LayoutView]:
    reply = guild_settings.reply
    container: discord.ui.Container[discord.ui.LayoutView] = discord.ui.Container(
        discord.ui.TextDisplay(
            "**Shared reply settings**\n"
            f"Mode: `{reply.mode.value}` · Timestamp: `{reply.show_timestamp}`\n"
            f"Footer: `{reply.footer_text or '(none)'}` · Icon source: `{reply.icon.source.value}`"
        )
    )

    mode_row: discord.ui.ActionRow[discord.ui.LayoutView] = discord.ui.ActionRow()
    mode_row.add_item(_mode_button(reply.mode))
    mode_row.add_item(_timestamp_button(reply.show_timestamp))
    mode_row.add_item(_edit_button(guild_settings))
    container.add_item(mode_row)

    icon_row: discord.ui.ActionRow[discord.ui.LayoutView] = discord.ui.ActionRow()
    icon_row.add_item(_icon_source_select(reply.icon.source))
    container.add_item(icon_row)

    mod_row: discord.ui.ActionRow[discord.ui.LayoutView] = discord.ui.ActionRow()
    mod_row.add_item(
        _role_select("Moderator roles", guild_settings.permissions.moderator_role_ids, "moderator")
    )
    container.add_item(mod_row)

    priv_row: discord.ui.ActionRow[discord.ui.LayoutView] = discord.ui.ActionRow()
    priv_row.add_item(
        _role_select(
            "Privileged roles", guild_settings.permissions.privileged_role_ids, "privileged"
        )
    )
    container.add_item(priv_row)

    return container


def _mode_button(current: ReplyMode) -> discord.ui.Button[discord.ui.LayoutView]:
    button: discord.ui.Button[discord.ui.LayoutView] = discord.ui.Button(
        label=f"Switch to {'text' if current is ReplyMode.EMBED else 'embed'}",
        style=discord.ButtonStyle.secondary,
    )

    async def callback(interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        corridor = _get_corridor(interaction)
        new_mode = ReplyMode.TEXT if current is ReplyMode.EMBED else ReplyMode.EMBED
        await corridor.set_reply_mode(interaction.guild.id, new_mode)
        await interaction.response.send_message(
            f"Reply mode set to `{new_mode.value}`.", ephemeral=True
        )

    button.callback = callback  # type: ignore[method-assign]
    return button


def _timestamp_button(current: bool) -> discord.ui.Button[discord.ui.LayoutView]:
    button: discord.ui.Button[discord.ui.LayoutView] = discord.ui.Button(
        label=f"Timestamp: {'on' if current else 'off'}",
        style=discord.ButtonStyle.secondary,
    )

    async def callback(interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        corridor = _get_corridor(interaction)
        await corridor.set_show_timestamp(interaction.guild.id, not current)
        await interaction.response.send_message(
            f"Timestamp turned {'off' if current else 'on'}.", ephemeral=True
        )

    button.callback = callback  # type: ignore[method-assign]
    return button


def _edit_button(guild_settings: GuildSettings) -> discord.ui.Button[discord.ui.LayoutView]:
    button: discord.ui.Button[discord.ui.LayoutView] = discord.ui.Button(
        label="Edit footer / custom icon", style=discord.ButtonStyle.primary
    )

    async def callback(interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(SharedSettingsModal(guild_settings))

    button.callback = callback  # type: ignore[method-assign]
    return button


def _icon_source_select(
    current: IconSource,
) -> discord.ui.Select[discord.ui.LayoutView]:
    select: discord.ui.Select[discord.ui.LayoutView] = discord.ui.Select(
        placeholder="Icon source",
        options=[
            discord.SelectOption(label=source.value, value=source.value, default=source is current)
            for source in IconSource
        ],
    )

    async def callback(interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        corridor = _get_corridor(interaction)
        settings = await corridor.guild_settings(interaction.guild.id)
        new_source = IconSource(select.values[0])
        await corridor.set_icon_preference(
            interaction.guild.id,
            IconPreference(source=new_source, custom_url=settings.reply.icon.custom_url),
        )
        await interaction.response.send_message(
            f"Icon source set to `{new_source.value}`.", ephemeral=True
        )

    select.callback = callback  # type: ignore[method-assign]
    return select


def _role_select(
    placeholder: str, current_role_ids: frozenset[int], tier: str
) -> discord.ui.RoleSelect[discord.ui.LayoutView]:
    select: discord.ui.RoleSelect[discord.ui.LayoutView] = discord.ui.RoleSelect(
        placeholder=placeholder,
        min_values=0,
        max_values=25,
        default_values=[discord.Object(id=role_id) for role_id in current_role_ids],
    )

    async def callback(interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        corridor = _get_corridor(interaction)
        role_ids = frozenset(role.id for role in select.values)
        if tier == "moderator":
            await corridor.set_moderator_role_ids(interaction.guild.id, role_ids)
        else:
            await corridor.set_privileged_role_ids(interaction.guild.id, role_ids)
        await interaction.response.send_message(f"{placeholder} updated.", ephemeral=True)

    select.callback = callback  # type: ignore[method-assign]
    return select


class SharedSettingsView(discord.ui.LayoutView):
    def __init__(self, guild_settings: GuildSettings) -> None:
        super().__init__()
        self.add_item(build_shared_settings_container(guild_settings))
