"""The `[p]cctv` command group -- listener status/configuration, guild
enablement, include-bots, rich-presence/message display, per-page clear
delays, and dashboard status (docs/cctv-design.md §2.8). Every command
that used to live under `[p]floorplan` for these same concerns moves
here; `[p]floorplan` itself keeps only Pixel Index browsing/catalogue
loading.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import discord
from discord import app_commands
from redbot.core import commands

from .cog_base import CogBase


class CommandsMixin(CogBase):
    """Requires `self._repository`, `self._reply`, `self._pixelagents`,
    `self._websocket_server`, `self._discord_client_hub`,
    `self._editor_client_hub`, `self._discord_office_service`,
    `self._webview_assets`, `self._sync_webview_assets`,
    `self._sync_all_guilds`, `self._full_sync`, `self._despawn_guild`,
    `self._authorize_discord_client` (all provided by `CogBase` or its
    sibling mixins)."""

    @staticmethod
    def _guild(ctx: commands.Context) -> discord.Guild:
        guild: discord.Guild | None = ctx.guild
        if guild is None:
            raise RuntimeError("This command requires a guild context")
        return guild

    @commands.hybrid_group(name="cctv", invoke_without_command=True)
    async def cctv_group(self, ctx: commands.Context) -> None:
        """Manage cctv's dashboard listener and the Discord page's presence mirroring."""

        send_help: Callable[[], Awaitable[object]] = ctx.send_help
        await send_help()

    @cctv_group.command(name="status")
    async def cmd_status(self, ctx: commands.Context) -> None:
        """Show cctv's listener, dashboard, and page status."""

        from corridor.domain import ReplyField

        await self._sync_webview_assets()
        settings = await self._repository.global_settings()
        assets_status = self._webview_assets_status()
        fields = [
            ReplyField("Office Server", f"{settings.host}:{settings.port}", False),
            ReplyField("Serving", "✅" if self._websocket_server.running else "🛑"),
            ReplyField(
                "Discord Page Clients",
                f"{self._discord_client_hub.client_count} "
                f"({self._discord_client_hub.editor_count} editor)",
            ),
            ReplyField("Editor Page Clients", str(self._editor_client_hub.client_count)),
            ReplyField("Assets", assets_status, code="[p]" in assets_status),
            ReplyField("Discord Clear Delay", f"{settings.discord_clear_delay}s"),
            ReplyField("Editor Clear Delay", f"{settings.editor_clear_delay}s"),
            ReplyField(
                "Broadcast Rich Presence", "✅" if settings.broadcast_rich_presence else "🛑"
            ),
            ReplyField("Broadcast Messages", "✅" if settings.broadcast_messages else "🛑"),
        ]
        if ctx.guild is not None:
            guild_settings = await self._repository.guild_settings(ctx.guild.id)
            fields.append(ReplyField("Guild Enabled", "✅" if guild_settings.enabled else "🛑"))
            fields.append(ReplyField("Include Bots", "✅" if guild_settings.include_bots else "🛑"))
        await self._reply.send_reply(ctx, title="cctv Status", fields=fields)

    @cctv_group.command(name="host")
    @commands.is_owner()
    @app_commands.describe(host="Host the office server binds (default: 127.0.0.1)")
    async def cmd_host(self, ctx: commands.Context, host: str) -> None:
        """Set the host the office server binds -- persist-only, reload to rebind."""

        await self._repository.set_host(host)
        await self._reply.send_reply(
            ctx, description=f"Host set to `{host}`. Reload the cog to rebind."
        )

    @cctv_group.command(name="port")
    @commands.is_owner()
    @app_commands.describe(port="Port the office server binds (default: 3210)")
    async def cmd_port(self, ctx: commands.Context, port: int) -> None:
        """Set the port the office server binds -- persist-only, reload to rebind."""

        try:
            await self._repository.set_port(port)
        except ValueError:
            await self._reply.send_reply(ctx, description="Port must be between 1 and 65535.")
            return
        await self._reply.send_reply(
            ctx,
            description=(
                f"Port set to `{port}`. Reload the cog to rebind, and update your reverse "
                "proxy's `/cctv/discord/ws` and `/cctv/editor/ws` routes to match."
            ),
        )

    @cctv_group.command(name="discordcleardelay")
    @commands.admin_or_permissions(administrator=True)
    @app_commands.describe(seconds="Seconds to keep the Discord page's activity indicator visible")
    async def cmd_discord_clear_delay(self, ctx: commands.Context, seconds: float) -> None:
        """Set how long the Discord page's message activity indicator stays visible."""

        try:
            await self._repository.set_discord_clear_delay(seconds)
        except ValueError:
            await self._reply.send_reply(ctx, description="Delay must be 0 or greater.")
            return
        await self._reply.send_reply(
            ctx, description=f"Discord page clear delay set to `{seconds}s`."
        )

    @cctv_group.command(name="editorcleardelay")
    @commands.admin_or_permissions(administrator=True)
    @app_commands.describe(seconds="Seconds to keep the editor page's activity indicator visible")
    async def cmd_editor_clear_delay(self, ctx: commands.Context, seconds: float) -> None:
        """Set how long the editor page's message activity indicator stays visible."""

        try:
            await self._repository.set_editor_clear_delay(seconds)
        except ValueError:
            await self._reply.send_reply(ctx, description="Delay must be 0 or greater.")
            return
        await self._reply.send_reply(
            ctx, description=f"Editor page clear delay set to `{seconds}s`."
        )

    @cctv_group.command(name="richpresence")
    @commands.admin_or_permissions(administrator=True)
    @app_commands.describe(value="Whether rich presence is shown on the Discord page")
    async def cmd_rich_presence(self, ctx: commands.Context, value: bool) -> None:
        """Set whether rich presence activity is broadcast to the Discord page."""

        await self._repository.set_broadcast_rich_presence(value)
        if not value:
            # Persisting the flag alone only stops *future* presence
            # updates from rendering -- whatever activity is already
            # displayed on connected Discord pages stays visible until
            # some other presence event happens to clear it. Clear it now.
            await self._discord_office_service.clear_presence()
        await self._reply.send_reply(
            ctx, description=f"Rich presence broadcasting set to `{value}`."
        )

    @cctv_group.command(name="messages")
    @commands.admin_or_permissions(administrator=True)
    @app_commands.describe(value="Whether Discord messages are shown on the Discord page")
    async def cmd_messages(self, ctx: commands.Context, value: bool) -> None:
        """Set whether Discord messages are broadcast as Discord-page tool bubbles."""

        await self._repository.set_broadcast_messages(value)
        await self._reply.send_reply(ctx, description=f"Message broadcasting set to `{value}`.")

    @cctv_group.command(name="enable")
    @commands.guild_only()
    @commands.admin_or_permissions(administrator=True)
    async def cmd_enable(self, ctx: commands.Context) -> None:
        """Enable Discord-page presence mirroring for this guild and run a full sync."""

        guild = self._guild(ctx)
        await self._repository.set_guild_enabled(guild.id, True)
        await self._reply.send_reply(ctx, description="Enabled. Running full sync…")
        await self._reply.send_reply(ctx, description=await self._full_sync(guild))

    @cctv_group.command(name="disable")
    @commands.guild_only()
    @commands.admin_or_permissions(administrator=True)
    async def cmd_disable(self, ctx: commands.Context) -> None:
        """Disable Discord-page presence mirroring for this guild and despawn its agents."""

        guild = self._guild(ctx)
        await self._repository.set_guild_enabled(guild.id, False)
        await self._despawn_guild(guild)
        # A keyholder already connected (and editing) via this guild may
        # have no *other* enabled guild to authorize through -- without
        # this, their socket keeps write access until it reconnects, even
        # though _can_edit_layout_user would now say no. Ported from
        # floorplan's own settings-change reauthorization (its
        # `_reauthorize_editors_after_settings_change`), dropped when this
        # cog was extracted; a keyholder role revoked directly in another
        # guild's member list still isn't detectable here -- corridor has
        # no event for that -- but this closes the one gap this command
        # itself can cause.
        await self._discord_client_hub.reauthorize(self._authorize_discord_client)
        await self._reply.send_reply(ctx, description="Disabled and despawned all tracked agents.")

    @cctv_group.command(name="includebots")
    @commands.guild_only()
    @commands.admin_or_permissions(administrator=True)
    @app_commands.describe(value="Whether bot users should be mirrored on the Discord page")
    async def cmd_include_bots(self, ctx: commands.Context, value: bool) -> None:
        """Set whether bot users are mirrored on the Discord page."""

        guild = self._guild(ctx)
        await self._repository.set_guild_include_bots(guild.id, value)
        await self._reply.send_reply(
            ctx, description=f"include_bots set to `{value}`. Running sync…"
        )
        await self._reply.send_reply(ctx, description=await self._full_sync(guild))

    @cctv_group.command(name="sync")
    @commands.guild_only()
    @commands.admin_or_permissions(administrator=True)
    async def cmd_sync(self, ctx: commands.Context) -> None:
        """Manually reconcile this guild's members against Discord presence."""

        guild = self._guild(ctx)
        if not await self._repository.guild_enabled(guild.id):
            await self._reply.send_reply(
                ctx, "Guild is not enabled. Enable it first:", code=["[p]cctv enable"]
            )
            return
        await self._reply.send_reply(ctx, description=await self._full_sync(guild))

    @cctv_group.command(name="despawnall")
    @commands.guild_only()
    @commands.admin_or_permissions(administrator=True)
    async def cmd_despawn_all(self, ctx: commands.Context) -> None:
        """Despawn this guild's tracked agents without disabling the cog."""

        guild = self._guild(ctx)
        await self._despawn_guild(guild)
        await self._reply.send_reply(
            ctx, description="Despawned all tracked agents for this guild."
        )


__all__ = ["CommandsMixin"]
