"""Discord-facing commands. Thin: translate ctx <-> service calls only.

Replies and permission checks go through corridor (this cog's required_cogs
dependency) rather than ctx.send()/hand-rolled role checks, so this cog
automatically respects whatever reply style and moderator/privileged roles
the guild has already configured for every other cog.
"""

from __future__ import annotations

from typing import Any

from redbot.core import commands

from corridor.domain import PermissionGroup

from ..application import CounterService


class CommandsMixin:
    """Requires `self._service: CounterService` and `self._corridor`
    (both provided by CogBase)."""

    _service: CounterService
    _corridor: Any

    @commands.hybrid_group(name="{{cookiecutter.cog_name}}")
    async def {{cookiecutter.cog_name}}_group(self, ctx: commands.Context) -> None:
        """{{ cookiecutter.short }}"""

        if ctx.invoked_subcommand is None:
            await ctx.send_help()

    @{{cookiecutter.cog_name}}_group.command(name="count")
    async def count(self, ctx: commands.Context) -> None:
        """Show this server's current count."""

        snapshot = await self._service.show(ctx.guild.id)
        await self._corridor.send_reply(ctx, title="Count", description=str(snapshot.count))

    @{{cookiecutter.cog_name}}_group.command(name="bump")
    async def bump(self, ctx: commands.Context) -> None:
        """Increment this server's count by one. Requires the moderator tier."""

        if not await self._corridor.require_permission(ctx, PermissionGroup.MODERATOR):
            return
        snapshot = await self._service.bump(ctx.guild.id)
        await self._corridor.send_reply(ctx, title="Count", description=f"Now: {snapshot.count}")
