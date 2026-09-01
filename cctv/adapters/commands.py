"""CCTV listener, display-policy, and guild commands."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import cast

import discord
from discord import app_commands
from redbot.core import commands

from corridor.domain import ReplyField

from .cog_base import CctvBase
from .dashboard import dashboard_cog_loaded


class CommandsMixin(CctvBase):
    @staticmethod
    def _guild(ctx: commands.Context) -> discord.Guild:
        if ctx.guild is None:
            raise RuntimeError("CCTV commands require a guild context")
        return cast(discord.Guild, ctx.guild)

    @commands.hybrid_group(name="cctv", invoke_without_command=True)
    @commands.guild_only()
    async def cctv_group(self, ctx: commands.Context) -> None:
        """Manage both Pixel Agents CCTV pages."""

        send_help: Callable[[], Awaitable[object]] = ctx.send_help
        await send_help()

    @cctv_group.command(name="status")
    @commands.admin_or_permissions(administrator=True)
    async def cmd_status(self, ctx: commands.Context) -> None:
        settings = await self._settings.global_settings()
        guild = await self._settings.guild_settings(self._guild(ctx))
        reasons = self._degraded_reasons()
        listener = self._server.running if self._server is not None else False
        fields = (
            ReplyField(
                "Listener",
                f"{settings.listener_host}:{settings.listener_port} "
                f"({'running' if listener else 'stopped'})",
            ),
            ReplyField("Discord WS", "/cctv/discord/ws"),
            ReplyField("Editor WS", "/cctv/editor/ws"),
            ReplyField("Discord Dashboard", "/third-party/cctv/discord"),
            ReplyField("Editor Dashboard", "/third-party/cctv/editor"),
            ReplyField(
                "Dashboard",
                "✅ loaded" if dashboard_cog_loaded(self.bot) else "⚠️ not loaded",
            ),
            ReplyField("Assets", "✅ loaded" if self._assets.ready else "⚠️ unavailable"),
            ReplyField(
                "Discord Pipeline",
                f"revision={self.discord_pipeline.revision}, "
                f"clients={self.discord_pipeline.clients.client_count}",
            ),
            ReplyField(
                "Editor Pipeline",
                f"revision={self.editor_pipeline.revision}, "
                f"clients={self.editor_pipeline.clients.client_count}",
            ),
            ReplyField("Guild Enabled", "✅" if guild.enabled else "🛑"),
            ReplyField("Include Bots", "✅" if guild.include_bots else "🛑"),
            ReplyField(
                "Rich Presence",
                "✅" if settings.broadcast_rich_presence else "🛑",
            ),
            ReplyField("Messages", "✅" if settings.broadcast_messages else "🛑"),
            ReplyField("Discord Clear Delay", f"{settings.discord_clear_delay}s"),
            ReplyField("Editor Clear Delay", f"{settings.editor_clear_delay}s"),
            ReplyField("Health", "✅ healthy" if not reasons else "⚠️ " + "; ".join(reasons)),
        )
        await self._reply(ctx, title="CCTV Status", fields=fields)

    @cctv_group.command(name="dashboard")
    @commands.admin_or_permissions(administrator=True)
    async def cmd_dashboard(self, ctx: commands.Context) -> None:
        status = "loaded and ready" if dashboard_cog_loaded(self.bot) else "not loaded"
        await self._reply(
            ctx,
            f"Red Web Dashboard is {status}. Pages:",
            code=["/third-party/cctv/discord", "/third-party/cctv/editor"],
        )

    @cctv_group.command(name="host")
    @commands.is_owner()
    async def cmd_host(self, ctx: commands.Context, host: str) -> None:
        try:
            clean = await self._settings.set_listener_host(host)
        except ValueError as exc:
            await self._reply(ctx, str(exc))
            return
        await self._reply(ctx, f"Listener host set to `{clean}`. Reload cctv to rebind.")

    @cctv_group.command(name="port")
    @commands.is_owner()
    async def cmd_port(self, ctx: commands.Context, port: int) -> None:
        try:
            await self._settings.set_listener_port(port)
        except ValueError as exc:
            await self._reply(ctx, str(exc))
            return
        await self._reply(ctx, f"Listener port set to `{port}`. Reload cctv to rebind.")

    @cctv_group.command(name="cleardelay")
    @commands.is_owner()
    @app_commands.describe(page="discord or editor", seconds="Seconds before clearing activity")
    async def cmd_clear_delay(self, ctx: commands.Context, page: str, seconds: float) -> None:
        try:
            await self._settings.set_clear_delay(page.lower(), seconds)
        except ValueError as exc:
            await self._reply(ctx, str(exc))
            return
        await self._reply(ctx, f"{page.lower()} clear delay set to `{seconds}s`.")

    @cctv_group.command(name="richpresence")
    @commands.admin_or_permissions(administrator=True)
    async def cmd_rich_presence(self, ctx: commands.Context, value: bool) -> None:
        await self._settings.set_broadcast_rich_presence(value)
        if value:
            await self._sync_all_guilds()
        else:
            await self.discord_pipeline.office.clear_presence()
        await self._reply(ctx, f"Rich-presence display set to `{value}`.")

    @cctv_group.command(name="messages")
    @commands.admin_or_permissions(administrator=True)
    async def cmd_messages(self, ctx: commands.Context, value: bool) -> None:
        await self._settings.set_broadcast_messages(value)
        await self._reply(ctx, f"Message display set to `{value}`.")

    @cctv_group.command(name="enable")
    @commands.admin_or_permissions(administrator=True)
    async def cmd_enable(self, ctx: commands.Context) -> None:
        guild = self._guild(ctx)
        await self._settings.set_guild_enabled(guild.id, True)
        await self.discord_pipeline.clients.reauthorize(self._can_edit_discord)
        await self._reply(ctx, await self._full_sync(guild))

    @cctv_group.command(name="disable")
    @commands.admin_or_permissions(administrator=True)
    async def cmd_disable(self, ctx: commands.Context) -> None:
        guild = self._guild(ctx)
        await self._settings.set_guild_enabled(guild.id, False)
        await self.discord_pipeline.clients.reauthorize(self._can_edit_discord)
        await self._despawn_guild(guild)
        await self._reply(ctx, "Guild disabled and its Discord roster despawned.")

    @cctv_group.command(name="includebots")
    @commands.admin_or_permissions(administrator=True)
    async def cmd_include_bots(self, ctx: commands.Context, value: bool) -> None:
        guild = self._guild(ctx)
        await self._settings.set_guild_include_bots(guild.id, value)
        if await self._settings.guild_enabled(guild):
            await self._reply(ctx, await self._full_sync(guild))
        else:
            await self._reply(ctx, f"Include-bots set to `{value}`.")

    @cctv_group.command(name="sync")
    @commands.admin_or_permissions(administrator=True)
    async def cmd_sync(self, ctx: commands.Context) -> None:
        guild = self._guild(ctx)
        if not await self._settings.guild_enabled(guild):
            await self._reply(ctx, "Guild is disabled. Enable it first.")
            return
        await self._reply(ctx, await self._full_sync(guild))

    @cctv_group.command(name="despawnall")
    @commands.admin_or_permissions(administrator=True)
    async def cmd_despawn_all(self, ctx: commands.Context) -> None:
        await self._despawn_guild(self._guild(ctx))
        await self._reply(ctx, "Guild roster despawned.")


__all__ = ["CommandsMixin"]
