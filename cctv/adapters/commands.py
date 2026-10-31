"""CCTV listener, display-policy, guild, and character commands."""

from __future__ import annotations

import io
from collections.abc import Awaitable, Callable
from typing import cast
from urllib.parse import quote

import discord
from discord import app_commands
from redbot.core import commands

from corridor.domain import ReplyField
from pixelagents.domain import AgentKey, GenuineAgentKey, OfficeIdentity
from pixelagents.domain.characters import CharacterRecord, PixatarFailure

from ..domain import GlobalSettings
from ..infrastructure import EDITOR_WS_PATH, PICKER_PATH, office_page_path, office_ws_path
from .cog_base import CctvBase, roster_warnings
from .dashboard import dashboard_cog_loaded

# The corridor permission group allowed to show/set/reset another member's
# character (`[p]cctv character member…`). The reserved `owner` group (bot
# owner or guild Administrator) always satisfies it too.
MEMBER_CHARACTER_GROUP = "building_manager"


def _pixatar_failure(exc: Exception) -> PixatarFailure | None:
    """`exc` if pixelagents raised it as a pixatar failure. Matched by name:
    a pixelagents reload re-executes its modules, so it raises a newer
    execution's `PixatarFailure` class than the one imported here."""

    return cast(PixatarFailure, exc) if type(exc).__name__ == "PixatarFailure" else None


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
        """Show CCTV's listener, dashboard, this server's office, and its settings."""

        settings = await self._settings.global_settings()
        guild_obj = self._guild(ctx)
        guild = await self._settings.guild_settings(guild_obj)
        warnings = roster_warnings(guild_obj, self.bot.intents)
        reasons = self._degraded_reasons()
        listener = self._server.running if self._server is not None else False
        office = self.office(guild_obj.id)
        fields = (
            ReplyField(
                "Listener",
                f"{settings.listener_host}:{settings.listener_port} "
                f"({'running' if listener else 'stopped'})",
            ),
            ReplyField("Office WS", office_ws_path(guild_obj.id)),
            ReplyField("Editor WS", EDITOR_WS_PATH),
            ReplyField("Office Page", self._page_link(settings, office_page_path(guild_obj.id))),
            ReplyField("Editor Page", self._page_link(settings, "/third-party/cctv/editor")),
            ReplyField(
                "Dashboard",
                "✅ loaded" if dashboard_cog_loaded(self.bot) else "⚠️ not loaded",
            ),
            ReplyField("Assets", "✅ loaded" if self._assets.ready else "⚠️ unavailable"),
            ReplyField(
                "Office",
                f"revision={office.revision}, clients={office.clients.client_count}"
                if office is not None
                else "closed (CCTV is disabled here)",
            ),
            ReplyField(
                "Editor Office",
                f"revision={self.editor_pipeline.revision}, "
                f"clients={self.editor_pipeline.clients.client_count}",
            ),
            ReplyField("Guild Enabled", "✅" if guild.enabled else "🛑"),
            ReplyField("Include Bots", "✅" if guild.include_bots else "🛑"),
            ReplyField("Ghost Bots", "✅" if guild.ghost_bots else "🛑"),
            ReplyField("Show Offline", "✅" if guild.show_offline else "🛑"),
            ReplyField("Ghost Offline", "✅" if guild.ghost_offline else "🛑"),
            ReplyField(
                "Member Cache",
                "✅ complete" if not warnings else "⚠️ " + " ".join(warnings),
            ),
            ReplyField("Rich Presence", "✅" if guild.broadcast_rich_presence else "🛑"),
            ReplyField("Messages", "✅" if guild.broadcast_messages else "🛑"),
            ReplyField("Discord Clear Delay", f"{settings.discord_clear_delay}s"),
            ReplyField("Editor Clear Delay", f"{settings.editor_clear_delay}s"),
            ReplyField("Health", "✅ healthy" if not reasons else "⚠️ " + "; ".join(reasons)),
        )
        await self._reply(ctx, title="CCTV Status", fields=fields)

    @cctv_group.command(name="dashboard")
    @commands.admin_or_permissions(administrator=True)
    async def cmd_dashboard(self, ctx: commands.Context) -> None:
        """Show whether Red Web Dashboard is loaded and CCTV's page links."""

        settings = await self._settings.global_settings()
        status = "loaded and ready" if dashboard_cog_loaded(self.bot) else "not loaded"
        await self._reply(
            ctx,
            f"Red Web Dashboard is {status}. Pages:",
            code=[
                self._page_link(settings, PICKER_PATH),
                self._page_link(settings, office_page_path(self._guild(ctx).id)),
                self._page_link(settings, "/third-party/cctv/editor"),
            ],
        )

    @cctv_group.command(name="url")
    @commands.guild_only()
    async def cmd_url(self, ctx: commands.Context) -> None:
        """Show the link to this server's Pixel Agents office."""

        guild = self._guild(ctx)
        settings = await self._settings.global_settings()
        guild_settings = await self._settings.guild_settings(guild)
        if not guild_settings.enabled:
            await self._reply(
                ctx,
                "CCTV isn't enabled on this server, so it has no office yet. "
                "An admin can open one with `[p]cctv enable`.",
            )
            return
        if not dashboard_cog_loaded(self.bot):
            await self._reply(
                ctx, "Red Web Dashboard isn't loaded, so the office page can't be served yet."
            )
            return
        lines = ["Anyone with this link can watch this server's office."]
        if guild_settings.broadcast_messages:
            lines.append(
                "Chat messages from every channel the bot can read appear there as speech "
                "bubbles; an admin can turn that off with `[p]cctv messages false`."
            )
        if settings.public_base_url is None:
            lines.append(
                "The bot owner hasn't set the Dashboard's public address yet "
                "(`[p]cctv publicurl`), so this is the path on the Dashboard's host."
            )
        await self._reply(
            ctx,
            "\n".join(lines),
            title="This server's office",
            code=[self._page_link(settings, office_page_path(guild.id))],
        )

    @cctv_group.command(name="publicurl")
    @commands.is_owner()
    async def cmd_public_url(self, ctx: commands.Context, url: str | None = None) -> None:
        """Set the address browsers reach the Dashboard at, e.g. https://dash.example.com.

        `[p]cctv url` builds each server's office link from it. Bot owner only.
        """

        if url is None:
            current = (await self._settings.global_settings()).public_base_url
            await self._reply(
                ctx,
                f"The Dashboard's public address is <{current}>."
                if current is not None
                else "The Dashboard's public address isn't set. Set it with "
                "`[p]cctv publicurl https://…`.",
            )
            return
        try:
            clean = await self._settings.set_public_base_url(url)
        except ValueError as exc:
            await self._reply(ctx, str(exc))
            return
        await self._reply(ctx, f"The Dashboard's public address is now <{clean}>.")

    @staticmethod
    def _page_link(settings: GlobalSettings, path: str) -> str:
        base = settings.public_base_url
        return f"{base}{path}" if base is not None else path

    @cctv_group.command(name="host")
    @commands.is_owner()
    async def cmd_host(self, ctx: commands.Context, host: str) -> None:
        """Set the listener's bind host. Bot owner only; requires a reload."""

        try:
            clean = await self._settings.set_listener_host(host)
        except ValueError as exc:
            await self._reply(ctx, str(exc))
            return
        await self._reply(ctx, f"Listener host set to `{clean}`. Reload cctv to rebind.")

    @cctv_group.command(name="port")
    @commands.is_owner()
    async def cmd_port(self, ctx: commands.Context, port: int) -> None:
        """Set the listener's bind port. Bot owner only; requires a reload."""

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
        """Set how long a page waits before clearing a stale activity. Bot owner only."""

        try:
            await self._settings.set_clear_delay(page.lower(), seconds)
        except ValueError as exc:
            await self._reply(ctx, str(exc))
            return
        await self._reply(ctx, f"{page.lower()} clear delay set to `{seconds}s`.")

    @cctv_group.command(name="richpresence")
    @commands.admin_or_permissions(administrator=True)
    async def cmd_rich_presence(self, ctx: commands.Context, value: bool) -> None:
        """Show or hide members' Discord rich presence on this server's office."""

        guild = self._guild(ctx)
        await self._settings.set_guild_broadcast_rich_presence(guild.id, value)
        office = self.office(guild.id)
        if office is not None:
            if value:
                await self._full_sync(guild)
            else:
                await office.office.clear_presence()
        await self._reply(ctx, f"Rich-presence display set to `{value}`.")

    @cctv_group.command(name="messages")
    @commands.admin_or_permissions(administrator=True)
    async def cmd_messages(self, ctx: commands.Context, value: bool) -> None:
        """Show or hide members' chat messages on this server's office.

        Messages come from every channel the bot can read, so turn this off
        if the office's viewers shouldn't see a channel's messages.
        """

        await self._settings.set_guild_broadcast_messages(self._guild(ctx).id, value)
        await self._reply(ctx, f"Message display set to `{value}`.")

    @cctv_group.command(name="enable")
    @commands.admin_or_permissions(administrator=True)
    async def cmd_enable(self, ctx: commands.Context) -> None:
        """Open this server's office and sync its current roster."""

        guild = self._guild(ctx)
        await self._settings.set_guild_enabled(guild.id, True)
        await self._reply(ctx, await self._full_sync(guild))

    @cctv_group.command(name="disable")
    @commands.admin_or_permissions(administrator=True)
    async def cmd_disable(self, ctx: commands.Context) -> None:
        """Close this server's office: its viewers are disconnected."""

        guild = self._guild(ctx)
        await self._settings.set_guild_enabled(guild.id, False)
        await self._close_office(guild.id)
        await self._reply(ctx, "Guild disabled and its office closed.")

    @cctv_group.command(name="includebots")
    @commands.admin_or_permissions(administrator=True)
    async def cmd_include_bots(self, ctx: commands.Context, value: bool) -> None:
        """Include or exclude bot accounts from this guild's CCTV roster."""

        guild = self._guild(ctx)
        await self._settings.set_guild_include_bots(guild.id, value)
        if await self._settings.guild_enabled(guild):
            await self._reply(ctx, await self._full_sync(guild))
        else:
            await self._reply(ctx, f"Include-bots set to `{value}`.")

    @cctv_group.command(name="showoffline")
    @commands.admin_or_permissions(administrator=True)
    async def cmd_show_offline(self, ctx: commands.Context, value: bool) -> None:
        """Show or hide offline members in this guild's CCTV roster."""

        guild = self._guild(ctx)
        await self._settings.set_guild_show_offline(guild.id, value)
        await self._reply_policy(ctx, guild, f"Show-offline set to `{value}`.")

    @cctv_group.command(name="ghostoffline")
    @commands.admin_or_permissions(administrator=True)
    async def cmd_ghost_offline(self, ctx: commands.Context, value: bool) -> None:
        """Draw offline members translucent (with showoffline on) or normally."""

        guild = self._guild(ctx)
        await self._settings.set_guild_ghost_offline(guild.id, value)
        await self._reply_policy(ctx, guild, f"Ghost-offline set to `{value}`.")

    @cctv_group.command(name="ghostbots")
    @commands.admin_or_permissions(administrator=True)
    async def cmd_ghost_bots(self, ctx: commands.Context, value: bool) -> None:
        """Draw this guild's bot accounts translucent or normally."""

        guild = self._guild(ctx)
        await self._settings.set_guild_ghost_bots(guild.id, value)
        await self._reply_policy(ctx, guild, f"Ghost-bots set to `{value}`.")

    async def _reply_policy(self, ctx: commands.Context, guild: discord.Guild, done: str) -> None:
        # The roster is re-synced so the change shows at once.
        if await self._settings.guild_enabled(guild):
            await self._reply(ctx, f"{done}\n{await self._full_sync(guild)}")
        else:
            await self._reply(ctx, done)

    @cctv_group.command(name="sync")
    @commands.admin_or_permissions(administrator=True)
    async def cmd_sync(self, ctx: commands.Context) -> None:
        """Force a full resync of this guild's Discord roster."""

        guild = self._guild(ctx)
        if not await self._settings.guild_enabled(guild):
            await self._reply(ctx, "Guild is disabled. Enable it first.")
            return
        await self._reply(ctx, await self._full_sync(guild))

    @cctv_group.command(name="despawnall")
    @commands.admin_or_permissions(administrator=True)
    async def cmd_despawn_all(self, ctx: commands.Context) -> None:
        """Despawn this guild's entire Discord roster without disabling it."""

        await self._despawn_guild(self._guild(ctx))
        await self._reply(ctx, "Guild roster despawned.")

    @cctv_group.command(name="designer")
    @commands.is_owner()
    async def cmd_designer(self, ctx: commands.Context, url: str) -> None:
        """Set the character designer `[p]cctv character` links to. Bot owner only."""

        try:
            clean = await self._settings.set_designer_url(url)
        except ValueError as exc:
            await self._reply(ctx, str(exc))
            return
        await self._reply(ctx, f"Character designer set to <{clean}>.")

    @cctv_group.group(name="character", invoke_without_command=True)
    @commands.guild_only()
    async def cmd_character(self, ctx: commands.Context) -> None:
        """Show your office character and its seed code."""

        identity = AgentKey(self._guild(ctx).id, ctx.author.id)
        record = await self._pixelagents.office_character(identity)
        if record is None:
            await self._reply(ctx, self._pixatar_unavailable())
            return
        await self._reply_character(ctx, record, "Your office character")

    @cmd_character.command(name="set")
    @commands.guild_only()
    @app_commands.describe(seed="A seed code (px.…), e.g. copied from the character designer")
    async def cmd_character_set(self, ctx: commands.Context, seed: str) -> None:
        """Wear the character a seed code stands for.

        Design one in Pixel Index's character designer, press "Copy seed",
        and paste it here.
        """

        identity = AgentKey(self._guild(ctx).id, ctx.author.id)
        try:
            record = await self._pixelagents.set_office_character_seed(identity, seed)
        except Exception as exc:
            failure = _pixatar_failure(exc)
            if failure is None:
                raise
            await self._reply(ctx, self._pixatar_failure_text("That seed didn't work", failure))
            return
        await self._refresh_character(identity)
        await self._reply_character(ctx, record, "Character updated")

    @cmd_character.command(name="reset")
    @commands.guild_only()
    async def cmd_character_reset(self, ctx: commands.Context) -> None:
        """Go back to the character dealt from your Discord user id."""

        identity = AgentKey(self._guild(ctx).id, ctx.author.id)
        try:
            record = await self._pixelagents.reset_office_character(identity)
        except Exception as exc:
            failure = _pixatar_failure(exc)
            if failure is None:
                raise
            await self._reply(
                ctx, self._pixatar_failure_text("Couldn't reset your character", failure)
            )
            return
        await self._refresh_character(identity)
        await self._reply_character(ctx, record, "Character reset")

    def _pixatar_unavailable(self) -> str:
        return (
            "Characters can't be dealt right now: pixatar is unavailable.\n"
            f"pixatar: {self._pixelagents.pixatar_status()}"
        )

    def _pixatar_failure_text(self, what: str, exc: PixatarFailure) -> str:
        # The seed was fine; pixatar itself couldn't run.
        if exc.code in {"cli-not-found", "cli-exited"}:
            return self._pixatar_unavailable()
        return f"{what}: {exc.message}"

    @cmd_character.command(name="agent")
    @commands.guild_only()
    @commands.is_owner()
    @app_commands.describe(agent="A registered agent key, e.g. architect")
    async def cmd_character_agent(self, ctx: commands.Context, agent: str) -> None:
        """Show an agent's office character. Bot owner only."""

        identity = await self._agent_identity(ctx, agent)
        if identity is None:
            return
        record = await self._pixelagents.office_character(identity)
        if record is None:
            await self._reply(ctx, self._pixatar_unavailable())
            return
        await self._reply_character(ctx, record, f"{agent}'s office character", agent=agent)

    @cmd_character.command(name="agentset")
    @commands.guild_only()
    @commands.is_owner()
    @app_commands.describe(
        agent="A registered agent key, e.g. architect",
        seed="A seed code (px.…), e.g. copied from the character designer",
    )
    async def cmd_character_agent_set(self, ctx: commands.Context, agent: str, seed: str) -> None:
        """Dress an agent in the character a seed code stands for. Bot owner only.

        The agent wears it on both CCTV pages and in its Discord replies.
        """

        identity = await self._agent_identity(ctx, agent)
        if identity is None:
            return
        try:
            record = await self._pixelagents.set_office_character_seed(identity, seed)
        except Exception as exc:
            failure = _pixatar_failure(exc)
            if failure is None:
                raise
            await self._reply(ctx, self._pixatar_failure_text("That seed didn't work", failure))
            return
        await self._refresh_character(identity)
        await self._reply_character(ctx, record, f"{agent}'s character updated", agent=agent)

    @cmd_character.command(name="agentreset")
    @commands.guild_only()
    @commands.is_owner()
    @app_commands.describe(agent="A registered agent key, e.g. architect")
    async def cmd_character_agent_reset(self, ctx: commands.Context, agent: str) -> None:
        """Go back to the character dealt from an agent's key. Bot owner only."""

        identity = await self._agent_identity(ctx, agent)
        if identity is None:
            return
        try:
            record = await self._pixelagents.reset_office_character(identity)
        except Exception as exc:
            failure = _pixatar_failure(exc)
            if failure is None:
                raise
            await self._reply(
                ctx, self._pixatar_failure_text("Couldn't reset the character", failure)
            )
            return
        await self._refresh_character(identity)
        await self._reply_character(ctx, record, f"{agent}'s character reset", agent=agent)

    @cmd_character.command(name="member")
    @commands.guild_only()
    @app_commands.describe(member="Any member of this server, bots included")
    async def cmd_character_member(self, ctx: commands.Context, member: discord.Member) -> None:
        """Show a member's office character. Building Manager only."""

        if not await self._corridor.require_permission(ctx, MEMBER_CHARACTER_GROUP):
            return
        record = await self._pixelagents.office_character(self._member_identity(ctx, member))
        if record is None:
            await self._reply(ctx, self._pixatar_unavailable())
            return
        await self._reply_character(
            ctx, record, f"{member.display_name}'s office character", member=member
        )

    @cmd_character.command(name="memberset")
    @commands.guild_only()
    @app_commands.describe(
        member="Any member of this server, bots included",
        seed="A seed code (px.…), e.g. copied from the character designer",
    )
    async def cmd_character_member_set(
        self, ctx: commands.Context, member: discord.Member, seed: str
    ) -> None:
        """Dress a member in the character a seed code stands for. Building Manager only.

        Bots included: the bot's own account wears it in its Discord replies too.
        """

        if not await self._corridor.require_permission(ctx, MEMBER_CHARACTER_GROUP):
            return
        identity = self._member_identity(ctx, member)
        try:
            record = await self._pixelagents.set_office_character_seed(identity, seed)
        except Exception as exc:
            failure = _pixatar_failure(exc)
            if failure is None:
                raise
            await self._reply(ctx, self._pixatar_failure_text("That seed didn't work", failure))
            return
        await self._refresh_character(identity)
        await self._reply_character(
            ctx, record, f"{member.display_name}'s character updated", member=member
        )

    @cmd_character.command(name="memberreset")
    @commands.guild_only()
    @app_commands.describe(member="Any member of this server, bots included")
    async def cmd_character_member_reset(
        self, ctx: commands.Context, member: discord.Member
    ) -> None:
        """Go back to the character dealt from a member's user id. Building Manager only."""

        if not await self._corridor.require_permission(ctx, MEMBER_CHARACTER_GROUP):
            return
        identity = self._member_identity(ctx, member)
        try:
            record = await self._pixelagents.reset_office_character(identity)
        except Exception as exc:
            failure = _pixatar_failure(exc)
            if failure is None:
                raise
            await self._reply(
                ctx, self._pixatar_failure_text("Couldn't reset the character", failure)
            )
            return
        await self._refresh_character(identity)
        await self._reply_character(
            ctx, record, f"{member.display_name}'s character reset", member=member
        )

    def _member_identity(self, ctx: commands.Context, member: discord.Member) -> AgentKey:
        # The identity the member's own `[p]cctv character` uses; for the bot's
        # account, the one its Discord replies and both pages show.
        return AgentKey(self._guild(ctx).id, member.id)

    async def _agent_identity(self, ctx: commands.Context, agent: str) -> OfficeIdentity | None:
        """A registered agent's key. Replies (and returns None) when it isn't one.
        Discord accounts, the bot's own included, go through `member…`."""

        keys = sorted(registered.agent_key for registered in self._corridor.list_agents())
        if agent in keys:
            return GenuineAgentKey(agent)
        known = ", ".join(f"`{key}`" for key in keys) or "none"
        await self._reply(ctx, f"No agent `{agent}`. Known agents: {known}.")
        return None

    async def _reply_character(
        self,
        ctx: commands.Context,
        record: CharacterRecord,
        title: str,
        *,
        agent: str | None = None,
        member: discord.Member | None = None,
    ) -> None:
        settings = await self._settings.global_settings()
        # By value: see `_pixatar_failure` on reloaded classes.
        derived = record.source == "derived"
        if member is not None:
            subject = f"{member.display_name}'s character is"
            origin = "dealt from their Discord user id" if derived else "a seed code set for them"
            command = f"[p]cctv character memberset {member.id} <seed>"
        elif agent is None:
            subject = "Your character is"
            origin = "dealt from your Discord user id" if derived else "a seed code you set"
            command = "[p]cctv character set <seed>"
        else:
            subject = f"{agent}'s character is"
            origin = "dealt from its key" if derived else "a seed code set for it"
            command = f"[p]cctv character agentset {agent} <seed>"
        files: list[discord.File] = []
        try:
            png = await self._pixelagents.office_character_avatar(record)
            files.append(discord.File(io.BytesIO(png), filename="character.png"))
        except Exception as exc:
            if _pixatar_failure(exc) is None:
                raise
        await self._reply(
            ctx,
            f"{subject} {origin}. Change it in the "
            f"[designer]({settings.designer_url}?seed={quote(record.seed)}), "
            "then copy its seed and run:",
            title=title,
            fields=(ReplyField("Seed", record.seed, code=True),),
            code=[command],
            files=files,
        )


__all__ = ["CommandsMixin"]
