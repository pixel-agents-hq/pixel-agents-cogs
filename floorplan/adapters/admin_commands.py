"""Administrative Floorplan command group and settings commands."""

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
            raise RuntimeError("Floorplan commands require a guild context")
        return guild

    @commands.hybrid_group(name="floorplan", invoke_without_command=True)
    @commands.guild_only()
    async def floorplan_group(self, ctx: commands.Context) -> None:
        """Manage the Pixel Agents office presence mirroring."""

        send_help: Callable[[], Awaitable[object]] = ctx.send_help
        await send_help()

    @floorplan_group.command(name="status")
    @commands.admin_or_permissions(administrator=True)
    async def cmd_status(self, ctx: commands.Context) -> None:
        """Show current Floorplan configuration and connection status."""

        # Deferred, not just top-level-import style: `ReplyField` is
        # constructed below, so (unlike replies.py's annotation-only usage)
        # this can't move under TYPE_CHECKING -- but importing it here
        # instead of at module scope means Red's `_cleanup_and_refresh_modules`
        # re-execing this module (which it does unconditionally on every
        # reload attempt, before setup() gets to run `ensure_corridor_loaded`)
        # never touches corridor either. The import only actually runs when
        # this command handler is invoked, by which point cog_load() has
        # already guaranteed corridor is loaded.
        from corridor.domain import ReplyField

        guild = self._guild(ctx)
        await self._sync_webview_assets()
        global_settings = await self._settings_service.global_settings()
        guild_settings = await self._settings_service.guild_settings(guild.id)
        tracked = sum(guild_id == guild.id for guild_id, _ in self._agents)

        def yn(value: bool) -> str:
            return "✅" if value else "🛑"

        # "[p]" in the raw status text (not yet prefix-substituted -- that
        # happens later, in corridor's render pipeline) is exactly the
        # cases where the status also names a command to run, e.g. an
        # outdated build convention or a failed build -- fence those so
        # Discord gives a copy button, instead of every status line.
        assets_status = self._webview_assets_status()
        fields = [
            ReplyField(
                "Office Server", f"{global_settings.ws_host}:{global_settings.ws_port}/ws", False
            ),
            ReplyField("Serving", yn(self._websocket_server.running)),
            ReplyField(
                "Office Clients",
                f"{self._client_hub.client_count} ({self._client_hub.editor_count} editor)",
            ),
            ReplyField("Assets", assets_status, code="[p]" in assets_status),
            ReplyField("Msg Tool Clear Delay", f"{global_settings.message_tool_clear_delay}s"),
            ReplyField("Guild Enabled", yn(guild_settings.enabled)),
            ReplyField("Include Bots", yn(guild_settings.include_bots)),
            ReplyField("Tracked Agents", str(tracked)),
            ReplyField("Broadcast Rich Presence", yn(global_settings.broadcast_rich_presence)),
            ReplyField("Broadcast Messages", yn(global_settings.broadcast_messages)),
            ReplyField("Pixel Index API", global_settings.pixel_index_api_url, False),
            ReplyField("Pixel Index Web", global_settings.pixel_index_web_url, False),
        ]
        await self._reply(ctx, title="Floorplan Status", fields=fields)

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

    @floorplan_group.command(name="settings")
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

    @floorplan_group.command(name="wsport")
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

    @floorplan_group.command(name="toolcleardelay")
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

    @floorplan_group.command(name="richpresence")
    @commands.admin_or_permissions(administrator=True)
    @app_commands.describe(
        value="Whether rich presence (Spotify, games, etc.) is shown in the webview"
    )
    async def cmd_richpresence(self, ctx: commands.Context, value: bool) -> None:
        """Set whether rich presence activity is broadcast to the webview."""

        await self._settings_service.set_broadcast_rich_presence(value)
        await self._reply(ctx, f"Rich presence broadcasting set to `{value}`.")

    @floorplan_group.command(name="messages")
    @commands.admin_or_permissions(administrator=True)
    @app_commands.describe(
        value="Whether Discord messages are shown as tool bubbles in the webview"
    )
    async def cmd_messages(self, ctx: commands.Context, value: bool) -> None:
        """Set whether Discord messages are broadcast as office tool bubbles."""

        await self._settings_service.set_broadcast_messages(value)
        await self._reply(ctx, f"Message broadcasting set to `{value}`.")

    @floorplan_group.command(name="enable")
    @commands.admin_or_permissions(administrator=True)
    async def cmd_enable(self, ctx: commands.Context) -> None:
        """Enable office presence mirroring and run a full guild sync."""

        guild = self._guild(ctx)
        if ctx.interaction:
            await ctx.interaction.response.defer(ephemeral=True)
        result = await self._settings_service.enable_guild(guild.id)
        await self._reply(ctx, "Enabled. Running full sync…")
        await self._reply(ctx, result)

    @floorplan_group.command(name="disable")
    @commands.admin_or_permissions(administrator=True)
    async def cmd_disable(self, ctx: commands.Context) -> None:
        """Disable office presence mirroring and despawn guild agents."""

        guild = self._guild(ctx)
        if ctx.interaction:
            await ctx.interaction.response.defer(ephemeral=True)
        await self._settings_service.disable_guild(guild.id)
        await self._reply(ctx, "Disabled. Despawning all tracked agents…")
        await self._reply(ctx, "Done.")

    @floorplan_group.command(name="includebots")
    @commands.admin_or_permissions(administrator=True)
    @app_commands.describe(value="Whether bot users should be mirrored")
    async def cmd_includebots(self, ctx: commands.Context, value: bool) -> None:
        """Set whether bot users are mirrored."""

        guild = self._guild(ctx)
        result = await self._settings_service.set_include_bots(guild.id, value)
        await self._reply(ctx, f"include_bots set to `{value}`. Running sync…")
        if result is not None:
            await self._reply(ctx, result)

    @floorplan_group.command(name="sync")
    @commands.admin_or_permissions(administrator=True)
    async def cmd_sync(self, ctx: commands.Context) -> None:
        """Manually reconcile guild members against Discord presence."""

        guild = self._guild(ctx)
        if ctx.interaction:
            await ctx.interaction.response.defer(ephemeral=True)
        if not await self._settings_repository.guild_enabled(guild):
            await self._reply(
                ctx, "Guild is not enabled. Enable it first:", code=["[p]floorplan enable"]
            )
            return
        await self._reply(ctx, "Syncing…")
        await self._reply(ctx, await self._full_sync(guild))

    @floorplan_group.command(name="despawnall")
    @commands.admin_or_permissions(administrator=True)
    async def cmd_despawnall(self, ctx: commands.Context) -> None:
        """Despawn guild agents without disabling the Cog."""

        guild = self._guild(ctx)
        if ctx.interaction:
            await ctx.interaction.response.defer(ephemeral=True)
        await self._reply(ctx, "Despawning all tracked agents for this guild…")
        await self._despawn_guild(guild)
        await self._reply(ctx, "Done.")
