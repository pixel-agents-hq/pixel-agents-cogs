"""Administrative Pixel Agents command group and settings commands."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import discord
from discord import app_commands
from redbot.core import commands

from .cog_base import PixelAgentsBase
from .settings_panel import SettingsPanelView, SettingsRuntimeSnapshot


class AdminCommandsMixin(PixelAgentsBase):
    """Keep the stable administrator command surface as a framework adapter."""

    @staticmethod
    def _guild(ctx: commands.Context) -> discord.Guild:
        """Narrow the guild guaranteed by the root command's guild-only check."""

        guild = ctx.guild
        if guild is None:
            raise RuntimeError("Pixel Agents commands require a guild context")
        return guild

    @commands.hybrid_group(name="pixelagents", invoke_without_command=True)
    @commands.guild_only()
    async def pixelagents_group(self, ctx: commands.Context) -> None:
        """Manage Pixelagents presence mirroring."""

        send_help: Callable[[], Awaitable[object]] = ctx.send_help
        await send_help()

    @pixelagents_group.command(name="status")
    @commands.admin_or_permissions(administrator=True)
    async def cmd_status(self, ctx: commands.Context) -> None:
        """Show current Pixelagents configuration and connection status."""

        guild = self._guild(ctx)
        global_settings = await self._settings_service.global_settings()
        guild_settings = await self._settings_service.guild_settings(guild.id)
        tracked = sum(guild_id == guild.id for guild_id, _ in self._agents)

        def yn(value: bool) -> str:
            return "✅" if value else "🛑"

        lines = [
            f"**Office Server:** {global_settings.ws_host}:{global_settings.ws_port}/ws",
            f"**Serving:** {yn(self._websocket_server.running)}",
            "**Office Clients:** "
            f"{self._client_hub.client_count} ({self._client_hub.editor_count} editor)",
            f"**Assets:** {self._webview_assets_status()}",
            f"**Msg Tool Clear Delay:** {global_settings.message_tool_clear_delay}s",
            f"**Guild Enabled:** {yn(guild_settings.enabled)}",
            f"**Include Bots:** {yn(guild_settings.include_bots)}",
            f"**Tracked Agents:** {tracked}",
            f"**Broadcast Rich Presence:** {yn(global_settings.broadcast_rich_presence)}",
            f"**Broadcast Messages:** {yn(global_settings.broadcast_messages)}",
            f"**Pixel Index API:** {global_settings.pixel_index_api_url}",
            f"**Pixel Index Web:** {global_settings.pixel_index_web_url}",
        ]
        await self._reply(ctx, "\n".join(lines), title="Pixelagents Status")

    def _settings_runtime_snapshot(self, guild_id: int) -> SettingsRuntimeSnapshot:
        return SettingsRuntimeSnapshot(
            serving=self._websocket_server.running,
            client_count=self._client_hub.client_count,
            editor_count=self._client_hub.editor_count,
            assets_loaded=bool(self._assets.get("characters")),
            tracked_agents=sum(
                1 for agent_guild_id, _ in self._agents if agent_guild_id == guild_id
            ),
        )

    @pixelagents_group.command(name="settings")
    @commands.admin_or_permissions(administrator=True)
    async def cmd_settings(self, ctx: commands.Context) -> None:
        """Open the interactive Pixel Agents administration panel."""

        guild = self._guild(ctx)
        view = await SettingsPanelView.create(
            self._settings_service,
            owner_id=ctx.author.id,
            guild_id=guild.id,
            runtime_snapshot=self._settings_runtime_snapshot,
        )
        await self._reply(ctx, view=view)

    @pixelagents_group.command(name="wsport")
    @commands.admin_or_permissions(administrator=True)
    @app_commands.describe(port="Port the office WebSocket server binds (default: 3210)")
    async def cmd_wsport(self, ctx: commands.Context, port: int) -> None:
        """Set the port the office WebSocket server listens on."""

        try:
            await self._settings_service.set_ws_port(port)
        except ValueError:
            await self._reply(ctx, "Port must be between 1 and 65535.")
            return
        await self._reply(
            ctx,
            f"Office server port set to `{port}`. Reload the cog to rebind, and update the "
            "Traefik `/ws` route to match.",
        )

    @pixelagents_group.command(name="toolcleardelay")
    @commands.admin_or_permissions(administrator=True)
    @app_commands.describe(seconds="Seconds to keep the message activity indicator visible")
    async def cmd_toolcleardelay(self, ctx: commands.Context, seconds: float) -> None:
        """Set how long a message activity indicator stays visible."""

        try:
            await self._settings_service.set_message_tool_clear_delay(seconds)
        except ValueError:
            await self._reply(ctx, "Delay must be 0 or greater.")
            return
        await self._reply(ctx, f"Message tool clear delay set to `{seconds}s`.")

    @pixelagents_group.command(name="richpresence")
    @commands.admin_or_permissions(administrator=True)
    @app_commands.describe(
        value="Whether rich presence (Spotify, games, etc.) is shown in the webview"
    )
    async def cmd_richpresence(self, ctx: commands.Context, value: bool) -> None:
        """Set whether rich presence activity is broadcast to the webview."""

        await self._settings_service.set_broadcast_rich_presence(value)
        await self._reply(ctx, f"Rich presence broadcasting set to `{value}`.")

    @pixelagents_group.command(name="messages")
    @commands.admin_or_permissions(administrator=True)
    @app_commands.describe(
        value="Whether Discord messages are shown as tool bubbles in the webview"
    )
    async def cmd_messages(self, ctx: commands.Context, value: bool) -> None:
        """Set whether Discord messages are broadcast as office tool bubbles."""

        await self._settings_service.set_broadcast_messages(value)
        await self._reply(ctx, f"Message broadcasting set to `{value}`.")

    @pixelagents_group.command(name="enable")
    @commands.admin_or_permissions(administrator=True)
    async def cmd_enable(self, ctx: commands.Context) -> None:
        """Enable office presence mirroring and run a full guild sync."""

        guild = self._guild(ctx)
        if ctx.interaction:
            await ctx.interaction.response.defer(ephemeral=True)
        result = await self._settings_service.enable_guild(guild.id)
        await self._reply(ctx, "Enabled. Running full sync…")
        await self._reply(ctx, result)

    @pixelagents_group.command(name="disable")
    @commands.admin_or_permissions(administrator=True)
    async def cmd_disable(self, ctx: commands.Context) -> None:
        """Disable office presence mirroring and despawn guild agents."""

        guild = self._guild(ctx)
        if ctx.interaction:
            await ctx.interaction.response.defer(ephemeral=True)
        await self._settings_service.disable_guild(guild.id)
        await self._reply(ctx, "Disabled. Despawning all tracked agents…")
        await self._reply(ctx, "Done.")

    @pixelagents_group.command(name="includebots")
    @commands.admin_or_permissions(administrator=True)
    @app_commands.describe(value="Whether bot users should be mirrored")
    async def cmd_includebots(self, ctx: commands.Context, value: bool) -> None:
        """Set whether bot users are mirrored."""

        guild = self._guild(ctx)
        result = await self._settings_service.set_include_bots(guild.id, value)
        await self._reply(ctx, f"include_bots set to `{value}`. Running sync…")
        if result is not None:
            await self._reply(ctx, result)

    @pixelagents_group.command(name="sync")
    @commands.admin_or_permissions(administrator=True)
    async def cmd_sync(self, ctx: commands.Context) -> None:
        """Manually reconcile guild members against Discord presence."""

        guild = self._guild(ctx)
        if ctx.interaction:
            await ctx.interaction.response.defer(ephemeral=True)
        if not await self._settings_repository.guild_enabled(guild):
            await self._reply(ctx, "Guild is not enabled. Use `[p]pixelagents enable` first.")
            return
        await self._reply(ctx, "Syncing…")
        await self._reply(ctx, await self._full_sync(guild))

    @pixelagents_group.command(name="despawnall")
    @commands.admin_or_permissions(administrator=True)
    async def cmd_despawnall(self, ctx: commands.Context) -> None:
        """Despawn guild agents without disabling the Cog."""

        guild = self._guild(ctx)
        if ctx.interaction:
            await ctx.interaction.response.defer(ephemeral=True)
        await self._reply(ctx, "Despawning all tracked agents for this guild…")
        await self._despawn_guild(guild)
        await self._reply(ctx, "Done.")

    @pixelagents_group.group(name="webview", invoke_without_command=True)
    @commands.admin_or_permissions(administrator=True)
    async def cmd_webview(self, ctx: commands.Context) -> None:
        """Manage the built Pixel Agents webview assets."""

        send_help: Callable[[], Awaitable[object]] = ctx.send_help
        await send_help()

    @cmd_webview.command(name="rebuild")
    @commands.admin_or_permissions(administrator=True)
    async def cmd_webview_rebuild(self, ctx: commands.Context) -> None:
        """Re-clone and rebuild the webview from the pinned Pixel Agents commit.

        Runs the same routine `cog_load` runs automatically -- useful after
        installing a missing build tool, or to pick up a pin bump without a
        full cog reload.
        """

        if ctx.interaction:
            await ctx.interaction.response.defer(ephemeral=True)
        await self._reply(ctx, "Rebuilding the Pixel Agents webview…")
        await self._reply(ctx, await self._rebuild_webview(force=True))
