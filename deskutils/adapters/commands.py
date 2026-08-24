"""Discord-facing commands. Thin: translate ctx <-> service calls only.

Replies go through corridor (this cog's required_cogs dependency) rather
than ctx.send(), so this cog automatically respects whatever reply style
the guild has already configured for every other cog.
"""

from __future__ import annotations

from typing import Annotated, Any

from redbot.core import commands

from corridor.domain import EMPLOYEE_KEY, ReplyField, ToolDescription, llm_tool

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
    @llm_tool(
        name="deskutils_time",
        description=(
            "Get the current date and time. Optionally pass an IANA timezone name "
            "(e.g. 'America/New_York') to also get it localized to that zone."
        ),
        required_group=EMPLOYEE_KEY,
    )
    async def time_command(
        self,
        ctx: commands.Context,
        timezone: Annotated[
            str | None,
            ToolDescription("An IANA time zone name, e.g. 'America/New_York' or 'Europe/London'."),
        ] = None,
    ) -> dict[str, object]:
        """Show the current time.

        Always includes Discord's native timestamp markup, which each
        viewer's own client renders in their own local time and locale
        automatically, plus an explicit UTC timestamp. Pass an IANA
        `timezone` (e.g. `America/New_York`) to also show it explicitly
        localized to that zone.
        """

        if not await self._corridor.require_permission(ctx, EMPLOYEE_KEY):
            return {
                "status": "error",
                "error": "permission_denied",
                "message": "The invoking member does not have permission to use this tool.",
            }

        snapshot = self._service.now()
        epoch = snapshot.epoch_seconds
        discord_timestamp = f"<t:{epoch}:F> (<t:{epoch}:R>)"
        utc = snapshot.utc.strftime("%Y-%m-%d %H:%M:%S %Z")
        fields = [
            ReplyField(
                "Discord (auto-localized per viewer)",
                discord_timestamp,
                inline=False,
            ),
            ReplyField("UTC", utc, inline=False),
        ]
        result: dict[str, object] = {
            "status": "ok",
            "epoch_seconds": epoch,
            "utc": utc,
            "discord_timestamp": discord_timestamp,
        }

        if timezone is not None:
            try:
                zone = self._service.resolve_zone(timezone)
            except UnknownTimeZoneError:
                warning = (
                    f"⚠️ Unknown time zone `{timezone}`. Use an IANA name, e.g. "
                    "`America/New_York` or `Europe/London`."
                )
                await self._corridor.send_reply(
                    ctx,
                    title="deskutils",
                    description=warning,
                )
                return {
                    "status": "error",
                    "error": "unknown_timezone",
                    "timezone": timezone,
                    "message": warning,
                }
            localized = snapshot.utc.astimezone(zone)
            localized_text = localized.strftime("%Y-%m-%d %H:%M:%S %Z")
            fields.append(ReplyField(timezone, localized_text, inline=False))
            result["timezone"] = timezone
            result["localized"] = localized_text

        await self._corridor.send_reply(ctx, title="Current time", fields=fields)
        return result
