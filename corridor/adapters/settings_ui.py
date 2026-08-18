"""Components V2 settings UI. Two entry points:

- `SharedSettingsView`: corridor's own `[p]corridor settings` panel.
- `build_shared_settings_container()`: the same controls as a mountable
  fragment any cog can append to its own LayoutView, so every generated
  cog's settings command shows "shared settings" + "this cog's own settings"
  in one panel without re-implementing these controls.

Both read/write through `bot.get_cog("Corridor")` resolved from the
interaction's client, so a mounted fragment is self-sufficient regardless of
which cog's view it lives in.

Permission groups are an open, per-guild list (Building Manager and
Keyholder by default) an admin can rename, add to, or delete from directly
in this panel -- group management is UI-only, there is no text-command
equivalent.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord

from ..domain import (
    RESERVED_GROUP_KEYS,
    GuildSettings,
    IconPreference,
    IconSource,
    PermissionGroupDef,
    ReplyMode,
)

if TYPE_CHECKING:
    from .cog_base import CogBase

MAX_PERMISSION_GROUPS = 10


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


class TierLabelsModal(discord.ui.Modal):
    """Rename the two reserved, non-role-backed tiers (Owner / Employee)."""

    def __init__(self, guild_settings: GuildSettings) -> None:
        super().__init__(title="Rename Owner / Employee tiers")
        self.owner_input: discord.ui.TextInput[TierLabelsModal] = discord.ui.TextInput(
            label="Owner tier display name",
            required=True,
            default=guild_settings.permissions.owner_label,
            max_length=50,
        )
        self.employee_input: discord.ui.TextInput[TierLabelsModal] = discord.ui.TextInput(
            label="Employee tier display name",
            required=True,
            default=guild_settings.permissions.employee_label,
            max_length=50,
        )
        self.add_item(self.owner_input)
        self.add_item(self.employee_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        corridor = _get_corridor(interaction)
        await corridor.set_owner_label(interaction.guild.id, self.owner_input.value)
        await corridor.set_employee_label(interaction.guild.id, self.employee_input.value)
        await interaction.response.send_message("Tier names updated.", ephemeral=True)


class AddGroupModal(discord.ui.Modal):
    def __init__(self) -> None:
        super().__init__(title="Add permission group")
        self.key_input: discord.ui.TextInput[AddGroupModal] = discord.ui.TextInput(
            label="Stable key (lowercase, letters/digits/underscore)",
            required=True,
            max_length=50,
        )
        self.label_input: discord.ui.TextInput[AddGroupModal] = discord.ui.TextInput(
            label="Display name",
            required=True,
            max_length=50,
        )
        self.add_item(self.key_input)
        self.add_item(self.label_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        corridor = _get_corridor(interaction)
        key = self.key_input.value.strip().lower()

        if not key or not all(char.isalnum() or char == "_" for char in key):
            await interaction.response.send_message(
                "Key must be lowercase letters, digits, or underscores.", ephemeral=True
            )
            return
        if key in RESERVED_GROUP_KEYS:
            await interaction.response.send_message(
                f"{key!r} is reserved and can't be used as a custom group key.", ephemeral=True
            )
            return

        existing = await corridor.list_permission_groups(interaction.guild.id)
        if len(existing) >= MAX_PERMISSION_GROUPS:
            await interaction.response.send_message(
                f"Cannot add more than {MAX_PERMISSION_GROUPS} permission groups.",
                ephemeral=True,
            )
            return
        if any(group.key == key for group in existing):
            await interaction.response.send_message(
                f"A group with key {key!r} already exists.", ephemeral=True
            )
            return

        try:
            await corridor.add_permission_group(interaction.guild.id, key, self.label_input.value)
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        await interaction.response.send_message(
            f"Added group {self.label_input.value!r} (key: `{key}`).", ephemeral=True
        )


class RenameGroupModal(discord.ui.Modal):
    def __init__(self, key: str, current_label: str) -> None:
        super().__init__(title=f"Rename {current_label}")
        self._key = key
        self.label_input: discord.ui.TextInput[RenameGroupModal] = discord.ui.TextInput(
            label="Display name",
            required=True,
            default=current_label,
            max_length=50,
        )
        self.add_item(self.label_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        corridor = _get_corridor(interaction)
        await corridor.set_group_label(interaction.guild.id, self._key, self.label_input.value)
        await interaction.response.send_message(
            f"Renamed to {self.label_input.value!r}.", ephemeral=True
        )


def build_shared_settings_container(
    guild_settings: GuildSettings,
) -> discord.ui.Container[discord.ui.LayoutView]:
    reply = guild_settings.reply
    permissions = guild_settings.permissions
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

    container.add_item(
        discord.ui.TextDisplay(
            "**Permission tiers**\n"
            f"`{permissions.owner_label}` (bot owner or Administrator) and "
            f"`{permissions.employee_label}` (everyone) are fixed and can only be renamed. "
            "The groups below are role-backed, independent tiers you can add, rename, "
            "reassign roles for, or delete. Deleting a group a cog depends on denies "
            "access for that check rather than erroring."
        )
    )

    tier_labels_row: discord.ui.ActionRow[discord.ui.LayoutView] = discord.ui.ActionRow()
    tier_labels_row.add_item(_rename_tier_labels_button(guild_settings))
    tier_labels_row.add_item(_add_group_button(guild_settings))
    container.add_item(tier_labels_row)

    for group in permissions.groups:
        role_row: discord.ui.ActionRow[discord.ui.LayoutView] = discord.ui.ActionRow()
        role_row.add_item(_role_select(group))
        container.add_item(role_row)

        manage_row: discord.ui.ActionRow[discord.ui.LayoutView] = discord.ui.ActionRow()
        manage_row.add_item(_rename_group_button(group))
        manage_row.add_item(_delete_group_button(group))
        container.add_item(manage_row)

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


def _rename_tier_labels_button(
    guild_settings: GuildSettings,
) -> discord.ui.Button[discord.ui.LayoutView]:
    button: discord.ui.Button[discord.ui.LayoutView] = discord.ui.Button(
        label="Rename Owner / Employee", style=discord.ButtonStyle.secondary
    )

    async def callback(interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(TierLabelsModal(guild_settings))

    button.callback = callback  # type: ignore[method-assign]
    return button


def _add_group_button(guild_settings: GuildSettings) -> discord.ui.Button[discord.ui.LayoutView]:
    disabled = len(guild_settings.permissions.groups) >= MAX_PERMISSION_GROUPS
    button: discord.ui.Button[discord.ui.LayoutView] = discord.ui.Button(
        label="Add group", style=discord.ButtonStyle.primary, disabled=disabled
    )

    async def callback(interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(AddGroupModal())

    button.callback = callback  # type: ignore[method-assign]
    return button


def _role_select(group: PermissionGroupDef) -> discord.ui.RoleSelect[discord.ui.LayoutView]:
    select: discord.ui.RoleSelect[discord.ui.LayoutView] = discord.ui.RoleSelect(
        placeholder=group.label,
        min_values=0,
        max_values=25,
        default_values=[discord.Object(id=role_id) for role_id in group.role_ids],
    )

    async def callback(interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        corridor = _get_corridor(interaction)
        role_ids = frozenset(role.id for role in select.values)
        await corridor.set_group_role_ids(interaction.guild.id, group.key, role_ids)
        await interaction.response.send_message(f"{group.label} roles updated.", ephemeral=True)

    select.callback = callback  # type: ignore[method-assign]
    return select


def _rename_group_button(group: PermissionGroupDef) -> discord.ui.Button[discord.ui.LayoutView]:
    button: discord.ui.Button[discord.ui.LayoutView] = discord.ui.Button(
        label=f"Rename {group.label}",
        style=discord.ButtonStyle.secondary,
    )

    async def callback(interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(RenameGroupModal(group.key, group.label))

    button.callback = callback  # type: ignore[method-assign]
    return button


def _delete_group_button(group: PermissionGroupDef) -> discord.ui.Button[discord.ui.LayoutView]:
    button: discord.ui.Button[discord.ui.LayoutView] = discord.ui.Button(
        label=f"Delete {group.label}",
        style=discord.ButtonStyle.danger,
    )

    async def callback(interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        corridor = _get_corridor(interaction)
        await corridor.remove_permission_group(interaction.guild.id, group.key)
        await interaction.response.send_message(
            f"Deleted group {group.label!r}. Any cog checking key `{group.key}` will now "
            "deny access for that check until a group with that key exists again.",
            ephemeral=True,
        )

    button.callback = callback  # type: ignore[method-assign]
    return button


class SharedSettingsView(discord.ui.LayoutView):
    def __init__(self, guild_settings: GuildSettings) -> None:
        super().__init__()
        self.add_item(build_shared_settings_container(guild_settings))
