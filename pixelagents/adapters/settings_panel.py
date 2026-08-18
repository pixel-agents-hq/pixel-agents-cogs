"""Discord Components V2 adapter for administering Pixel Agents settings."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from math import isfinite
from typing import Any, TypeAlias, cast

import discord

from ..application.settings import SettingsService
from ..domain import GlobalSettings, GuildSettings

log = logging.getLogger("red.d_cogs.pixelagents.settings_panel")

MutationResult: TypeAlias = str | None
Mutation: TypeAlias = Callable[[], Awaitable[MutationResult]]
RuntimeSnapshotCallback: TypeAlias = Callable[[int], "SettingsRuntimeSnapshot"]
SettingValue: TypeAlias = str | int | float


@dataclass(frozen=True, slots=True)
class SettingsRuntimeSnapshot:
    """Small immutable status projection supplied by the runtime adapter."""

    serving: bool
    client_count: int
    editor_count: int
    assets_loaded: bool
    tracked_agents: int


def _enabled(value: bool) -> str:
    return "Enabled ✅" if value else "Disabled 🛑"


def _inline_code(value: object) -> str:
    """Render administrator-controlled values without breaking inline Markdown."""

    return f"`{str(value).replace('`', 'ˋ')}`"


class SettingsValueModal(discord.ui.Modal):  # type: ignore[misc, unused-ignore]
    """Single-value modal whose parsing and mutation stay with its owning view."""

    def __init__(
        self,
        panel: SettingsPanelView,
        *,
        title: str,
        label: str,
        description: str,
        current: str,
        parser: Callable[[str], SettingValue],
        apply: Callable[[SettingValue], Awaitable[MutationResult]],
    ) -> None:
        super().__init__(title=title, timeout=180)
        self._panel = panel
        self._parser = parser
        self._apply = apply
        self.value_input: discord.ui.TextInput[SettingsValueModal] = discord.ui.TextInput(
            default=current,
            required=True,
            min_length=1,
            max_length=500,
        )
        self.add_item(
            discord.ui.Label(
                text=label,
                description=description,
                component=self.value_input,
            )
        )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            value = self._parser(self.value_input.value)
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return

        async def mutation() -> MutationResult:
            return await self._apply(value)

        await self._panel.apply_mutation(interaction, mutation)


class SettingsPanelView(discord.ui.LayoutView):  # type: ignore[misc, unused-ignore]
    """Owner-scoped, self-refreshing Pixel Agents administration panel."""

    def __init__(
        self,
        service: SettingsService,
        *,
        owner_id: int,
        guild_id: int,
        global_settings: GlobalSettings,
        guild_settings: GuildSettings,
        runtime: SettingsRuntimeSnapshot | None,
        runtime_snapshot: RuntimeSnapshotCallback | None = None,
    ) -> None:
        super().__init__(timeout=180)
        self.service = service
        self.owner_id = owner_id
        self.guild_id = guild_id
        self.global_settings = global_settings
        self.guild_settings = guild_settings
        self.runtime = runtime
        self._runtime_snapshot = runtime_snapshot
        self._stale = False
        self._busy = False
        self._build()

    @classmethod
    async def create(
        cls,
        service: SettingsService,
        *,
        owner_id: int,
        guild_id: int,
        runtime_snapshot: RuntimeSnapshotCallback | None = None,
    ) -> SettingsPanelView:
        global_settings = await service.global_settings()
        guild_settings = await service.guild_settings(guild_id)
        runtime = runtime_snapshot(guild_id) if runtime_snapshot is not None else None
        return cls(
            service,
            owner_id=owner_id,
            guild_id=guild_id,
            global_settings=global_settings,
            guild_settings=guild_settings,
            runtime=runtime,
            runtime_snapshot=runtime_snapshot,
        )

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "Only the administrator who opened this panel can use its controls.",
                ephemeral=True,
            )
            return False
        if self._stale or self._busy:
            await interaction.response.send_message(
                "This settings panel is no longer active. Use the refreshed panel instead.",
                ephemeral=True,
            )
            return False
        return True

    async def on_timeout(self) -> None:
        self._stale = True

    def _build(self) -> None:
        global_settings = self.global_settings
        guild_settings = self.guild_settings
        container: discord.ui.Container[SettingsPanelView] = discord.ui.Container(
            discord.ui.TextDisplay(
                "# Pixel Agents settings\nChanges apply to this guild unless noted."
            ),
            accent_colour=discord.Color.blurple(),
        )

        if self.runtime is not None:
            runtime = self.runtime
            container.add_item(
                discord.ui.TextDisplay(
                    "**Runtime status**\n"
                    f"Server: {_enabled(runtime.serving)} · "
                    f"Clients: {runtime.client_count} ({runtime.editor_count} editors) · "
                    f"Assets: {'Loaded ✅' if runtime.assets_loaded else 'Missing ⚠️'} · "
                    f"Tracked here: {runtime.tracked_agents}"
                )
            )

        container.add_item(
            discord.ui.Section(
                discord.ui.TextDisplay(
                    "**Office server (global)**\n"
                    f"Host: {_inline_code(global_settings.ws_host)} (read-only) · "
                    f"Port: {_inline_code(global_settings.ws_port)}\n"
                    "Port changes require a cog reload/rebind and matching proxy configuration."
                ),
                accessory=discord.ui.Button(label="Host read-only", disabled=True),
            )
        )
        network_actions: discord.ui.ActionRow[SettingsPanelView] = discord.ui.ActionRow()
        network_actions.add_item(
            self._button("Edit port", self._show_port_modal, discord.ButtonStyle.primary)
        )
        network_actions.add_item(self._button("Edit clear delay", self._show_delay_modal))
        container.add_item(network_actions)

        container.add_item(
            discord.ui.TextDisplay(
                "**Broadcasting (global)**\n"
                f"Rich presence: {_enabled(global_settings.broadcast_rich_presence)} · "
                f"Messages: {_enabled(global_settings.broadcast_messages)}\n\n"
                "**This guild**\n"
                f"Presence mirroring: {_enabled(guild_settings.enabled)} · "
                f"Include bots: {_enabled(guild_settings.include_bots)}\n\n"
                "**Message activity (global)**\n"
                f"Clear delay: {_inline_code(global_settings.message_tool_clear_delay)} seconds"
            )
        )
        toggles: discord.ui.ActionRow[SettingsPanelView] = discord.ui.ActionRow()
        toggles.add_item(
            self._button(
                "Toggle rich presence",
                self._toggle_rich_presence,
                discord.ButtonStyle.secondary,
            )
        )
        toggles.add_item(self._button("Toggle messages", self._toggle_messages))
        toggles.add_item(
            self._button(
                "Enable guild" if not guild_settings.enabled else "Disable guild",
                self._toggle_guild,
            )
        )
        toggles.add_item(self._button("Toggle bots", self._toggle_bots))
        container.add_item(toggles)

        container.add_item(
            discord.ui.Section(
                discord.ui.TextDisplay(
                    "**Pixel Index (global)**\n"
                    f"API: {_inline_code(global_settings.pixel_index_api_url)}\n"
                    f"Web: {_inline_code(global_settings.pixel_index_web_url)}"
                ),
                accessory=discord.ui.Button(label="HTTP(S) only", disabled=True),
            )
        )
        index_actions: discord.ui.ActionRow[SettingsPanelView] = discord.ui.ActionRow()
        index_actions.add_item(self._button("Edit API URL", self._show_api_url_modal))
        index_actions.add_item(self._button("Edit web URL", self._show_web_url_modal))
        container.add_item(index_actions)
        self.add_item(container)

    def _button(
        self,
        label: str,
        callback: Callable[[discord.Interaction], Awaitable[None]],
        style: discord.ButtonStyle = discord.ButtonStyle.secondary,
    ) -> discord.ui.Button[SettingsPanelView]:
        button: discord.ui.Button[SettingsPanelView] = discord.ui.Button(label=label, style=style)
        cast(Any, button).callback = callback
        return button

    async def _show_port_modal(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(
            SettingsValueModal(
                self,
                title="Office WebSocket port",
                label="Port",
                description="Enter an integer from 1 through 65535.",
                current=str(self.global_settings.ws_port),
                parser=self._parse_port,
                apply=self._set_port,
            )
        )

    async def _show_delay_modal(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(
            SettingsValueModal(
                self,
                title="Message activity clear delay",
                label="Seconds",
                description="Enter zero or a positive number.",
                current=str(self.global_settings.message_tool_clear_delay),
                parser=self._parse_delay,
                apply=self._set_delay,
            )
        )

    async def _show_api_url_modal(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(
            SettingsValueModal(
                self,
                title="Pixel Index API URL",
                label="Absolute HTTP(S) URL",
                description="The API base URL; a trailing slash is optional.",
                current=self.global_settings.pixel_index_api_url,
                parser=self._parse_url,
                apply=self._set_api_url,
            )
        )

    async def _show_web_url_modal(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(
            SettingsValueModal(
                self,
                title="Pixel Index web URL",
                label="Absolute HTTP(S) URL",
                description="The web frontend base URL; a trailing slash is optional.",
                current=self.global_settings.pixel_index_web_url,
                parser=self._parse_url,
                apply=self._set_web_url,
            )
        )

    @staticmethod
    def _parse_port(raw: str) -> int:
        try:
            port = int(raw.strip())
        except ValueError as exc:
            raise ValueError("Port must be a whole number between 1 and 65535.") from exc
        if not 1 <= port <= 65535:
            raise ValueError("Port must be between 1 and 65535.")
        return port

    @staticmethod
    def _parse_delay(raw: str) -> float:
        try:
            delay = float(raw.strip())
        except ValueError as exc:
            raise ValueError("Delay must be a number that is 0 or greater.") from exc
        if not isfinite(delay) or delay < 0:
            raise ValueError("Delay must be 0 or greater.")
        return delay

    @staticmethod
    def _parse_url(raw: str) -> str:
        value = raw.strip()
        if not value:
            raise ValueError("URL cannot be empty.")
        return value

    async def _set_port(self, value: SettingValue) -> MutationResult:
        await self.service.set_ws_port(int(value))
        return "Port saved. Reload the cog to rebind the office server."

    async def _set_delay(self, value: SettingValue) -> MutationResult:
        await self.service.set_message_tool_clear_delay(float(value))
        return None

    async def _set_api_url(self, value: SettingValue) -> MutationResult:
        await self.service.set_pixel_index_api_url(str(value))
        return None

    async def _set_web_url(self, value: SettingValue) -> MutationResult:
        await self.service.set_pixel_index_web_url(str(value))
        return None

    async def _toggle_rich_presence(self, interaction: discord.Interaction) -> None:
        value = not self.global_settings.broadcast_rich_presence

        async def mutation() -> MutationResult:
            await self.service.set_broadcast_rich_presence(value)
            return None

        await self.apply_mutation(interaction, mutation, slow=not value)

    async def _toggle_messages(self, interaction: discord.Interaction) -> None:
        value = not self.global_settings.broadcast_messages

        async def mutation() -> MutationResult:
            await self.service.set_broadcast_messages(value)
            return None

        await self.apply_mutation(interaction, mutation)

    async def _toggle_guild(self, interaction: discord.Interaction) -> None:
        if self.guild_settings.enabled:

            async def mutation() -> MutationResult:
                await self.service.disable_guild(self.guild_id)
                return "Guild disabled and its tracked agents were despawned."

        else:

            async def mutation() -> MutationResult:
                return await self.service.enable_guild(self.guild_id)

        await self.apply_mutation(interaction, mutation, slow=True)

    async def _toggle_bots(self, interaction: discord.Interaction) -> None:
        value = not self.guild_settings.include_bots

        async def mutation() -> MutationResult:
            return await self.service.set_include_bots(self.guild_id, value)

        await self.apply_mutation(
            interaction,
            mutation,
            slow=self.guild_settings.enabled,
        )

    async def apply_mutation(
        self,
        interaction: discord.Interaction,
        mutation: Mutation,
        *,
        slow: bool = False,
    ) -> None:
        """Apply once, then atomically retire and replace this view."""

        if self._stale or self._busy:
            await interaction.response.send_message(
                "This settings panel is no longer active. Use the refreshed panel instead.",
                ephemeral=True,
            )
            return
        self._busy = True
        if slow:
            await interaction.response.defer(ephemeral=True)
        try:
            result = await mutation()
        except ValueError as exc:
            self._busy = False
            await self._send_error(interaction, str(exc))
            return
        except Exception:
            self._stale = True
            self.stop()
            log.exception("Failed to update Pixel Agents settings")
            await self._send_error(
                interaction,
                "The setting could not be fully updated. Run the settings command again "
                "before retrying.",
            )
            return

        self._stale = True
        self.stop()
        try:
            refreshed = await type(self).create(
                self.service,
                owner_id=self.owner_id,
                guild_id=self.guild_id,
                runtime_snapshot=self._runtime_snapshot,
            )
        except Exception:
            log.exception("Updated Pixel Agents settings but failed to refresh the panel")
            await self._send_error(
                interaction,
                "The setting was updated, but the panel could not refresh. "
                "Run the settings command again.",
            )
            return
        if interaction.response.is_done():
            await interaction.edit_original_response(view=refreshed)
        else:
            await interaction.response.edit_message(view=refreshed)
        if result:
            await interaction.followup.send(result, ephemeral=True)

    @staticmethod
    async def _send_error(interaction: discord.Interaction, message: str) -> None:
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)


__all__ = [
    "RuntimeSnapshotCallback",
    "SettingsPanelView",
    "SettingsRuntimeSnapshot",
    "SettingsValueModal",
]
