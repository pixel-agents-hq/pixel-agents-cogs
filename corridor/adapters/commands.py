"""corridor's own commands. Configuration is also reachable by mounting
build_shared_settings_container() inside another cog's settings panel --
this is the standalone entry point for guild admins who just want corridor's
panel directly."""

from __future__ import annotations

from redbot.core import commands

from .settings_ui import SharedSettingsView


class CommandsMixin:
    """Requires `self.guild_settings` (provided by CogBase)."""

    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    @commands.hybrid_command(name="corridorsettings")
    async def corridor_settings(self, ctx: commands.Context) -> None:
        """Configure shared reply style and permission role tiers."""

        assert ctx.guild is not None
        settings = await self.guild_settings(ctx.guild.id)  # type: ignore[attr-defined]
        await ctx.send(view=SharedSettingsView(settings))
