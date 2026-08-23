"""Discord-facing commands. Thin: translate ctx <-> service calls only.

Replies go through corridor (this cog's required_cogs dependency) rather
than ctx.send(), so this cog automatically respects whatever reply style
the guild has already configured for every other cog.
"""

from __future__ import annotations

from typing import Any

from redbot.core import commands

from corridor.domain import ReplyField

from ..application import TimeService, UnknownTimeZoneError


class CommandsMixin:
    """Requires `self._service: TimeService` and `self._corridor`
    (both provided by CogBase)."""

    _service: TimeService
    _corridor: Any

    @commands.hybrid_group(name="deskutils")
    async def deskutils_group(self, ctx: commands.Context) -> None:
        """Get the current time in Discord-native and timezone-aware formats."""

        if ctx.invoked_subcommand is None:
            await ctx.send_help()

    @deskutils_group.command(name="time")
    async def time_command(self, ctx: commands.Context, timezone: str | None = None) -> None:
        """Show the current time.

        Always includes Discord's native timestamp markup, which each
        viewer's own client renders in their own local time and locale
        automatically, plus an explicit UTC timestamp. Pass an IANA
        `timezone` (e.g. `America/New_York`) to also show it explicitly
        localized to that zone.
        """

        snapshot = self._service.now()
        epoch = snapshot.epoch_seconds
        fields = [
            ReplyField(
                "Discord (auto-localized per viewer)",
                f"<t:{epoch}:F> (<t:{epoch}:R>)",
                inline=False,
            ),
            ReplyField("UTC", snapshot.utc.strftime("%Y-%m-%d %H:%M:%S %Z"), inline=False),
        ]

        if timezone is not None:
            try:
                zone = self._service.resolve_zone(timezone)
            except UnknownTimeZoneError:
                await self._corridor.send_reply(
                    ctx,
                    title="deskutils",
                    description=(
                        f"⚠️ Unknown time zone `{timezone}`. Use an IANA name, e.g. "
                        "`America/New_York` or `Europe/London`."
                    ),
                )
                return
            localized = snapshot.utc.astimezone(zone)
            fields.append(
                ReplyField(timezone, localized.strftime("%Y-%m-%d %H:%M:%S %Z"), inline=False)
            )

        await self._corridor.send_reply(ctx, title="Current time", fields=fields)
