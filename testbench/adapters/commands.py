"""Discord-facing commands. Thin: translate ctx <-> application-layer calls
only.

Owner-only and guild-only: every corridor event needs a guild_id (from the
invoking guild), and the whole point of this cog is seeing an event land
on a real guild's floorplan canvas."""

from __future__ import annotations

from typing import Any

from redbot.core import commands

from ..application import list_publishable_events
from .views import EventPickerView


class CommandsMixin:
    """Requires `self._corridor` (provided by CogBase)."""

    _corridor: Any
    _reply: Any

    @commands.hybrid_group(name="testbench")
    @commands.guild_only()
    @commands.is_owner()
    async def testbench_group(self, ctx: commands.Context) -> None:
        """Publish corridor bus events manually, for testing."""

        if ctx.invoked_subcommand is None:
            await ctx.send_help()

    @testbench_group.command(name="publish")
    async def publish(self, ctx: commands.Context) -> None:
        """Pick a corridor event type and publish it, filling in its fields
        through a Discord UI generated from corridor's own event catalog."""

        await ctx.send(view=EventPickerView())

    @testbench_group.command(name="list")
    async def list_events(self, ctx: commands.Context) -> None:
        """Show every event testbench can publish, auto-derived from
        corridor's event catalog."""

        specs = list_publishable_events()
        description = "\n".join(
            f"- **{spec.name}**: {', '.join(field.name for field in spec.fields)}" for spec in specs
        )
        await self._reply.send_reply(
            ctx, title="Publishable corridor events", description=description
        )
