"""Discord-facing commands. Thin: translate ctx <-> service calls only.

Replies and permission checks go through corridor (this cog's required_cogs
dependency) rather than ctx.send()/hand-rolled role checks, so this cog
automatically respects whatever reply style and permission groups the guild
has already configured for every other cog.
"""

from __future__ import annotations

from typing import Annotated, Any

from redbot.core import commands

from corridor.domain import EMPLOYEE_KEY, ToolDescription, llm_tool

from ..application import CounterService

_MIN_PROJECTION = 1
_MAX_PROJECTION = 10
_REPORT_STYLES = ("compact", "detailed")


def _permission_denied_result() -> dict[str, object]:
    return {
        "status": "error",
        "error": "permission_denied",
        "message": "The invoking member does not have permission to use this tool.",
    }


class CommandsMixin:
    """Requires `self._service: CounterService`, `self._corridor`, and
    `self._reply` (all provided by CogBase)."""

    _service: CounterService
    _corridor: Any
    _reply: Any

    @commands.hybrid_group(name="{{cookiecutter.cog_name}}")
    async def {{cookiecutter.cog_name}}_group(self, ctx: commands.Context) -> None:
        """{{ cookiecutter.short }}"""

        if ctx.invoked_subcommand is None:
            await ctx.send_help()

    @{{cookiecutter.cog_name}}_group.command(name="count")
    async def count(self, ctx: commands.Context) -> None:
        """Show this server's current count."""

        snapshot = await self._service.show(ctx.guild.id)
        await self._reply.send_reply(ctx, title="Count", description=str(snapshot.count))

    # The three decorated commands below are intentionally different
    # examples: no input (`bump`), bounded numeric input (`project`), and
    # enum-constrained string input (`report`). `@llm_tool` derives the LLM
    # input schema from their signatures while each remains a normal
    # Discord command. Tool calls invoke these exact callbacks, so keep the
    # explicit permission checks and validate values inside the callback;
    # ToolDescription shapes the advertised schema but is not runtime
    # validation. Returning a string-keyed mapping gives the LLM an
    # informational result in addition to the Discord reply.

    @{{cookiecutter.cog_name}}_group.command(name="bump")
    @llm_tool(
        name="{{cookiecutter.cog_name}}_bump",
        description="Increment this server's count by one.",
        required_group="keyholder",
    )
    async def bump(self, ctx: commands.Context) -> dict[str, object]:
        """Increment this server's count by one. Requires the keyholder tier."""

        if not await self._corridor.require_permission(ctx, "keyholder"):
            return _permission_denied_result()
        snapshot = await self._service.bump(ctx.guild.id)
        await self._reply.send_reply(ctx, title="Count", description=f"Now: {snapshot.count}")
        return {"status": "ok", "count": snapshot.count}

    @{{cookiecutter.cog_name}}_group.command(name="project")
    @llm_tool(
        name="{{cookiecutter.cog_name}}_project",
        description="Project the count after a number of future increments without changing it.",
        required_group=EMPLOYEE_KEY,
    )
    async def project(
        self,
        ctx: commands.Context,
        amount: Annotated[
            int,
            ToolDescription(
                "The number of increments to project.",
                minimum=_MIN_PROJECTION,
                maximum=_MAX_PROJECTION,
            ),
        ],
    ) -> dict[str, object]:
        """Project the count after 1-10 increments without changing it."""

        if not await self._corridor.require_permission(ctx, EMPLOYEE_KEY):
            return _permission_denied_result()
        if (
            isinstance(amount, bool)
            or not isinstance(amount, int)
            or not _MIN_PROJECTION <= amount <= _MAX_PROJECTION
        ):
            message = "Amount must be a whole number from 1 through 10."
            await self._reply.send_reply(ctx, title="Count projection", description=message)
            return {"status": "error", "error": "invalid_amount", "message": message}

        snapshot = await self._service.show(ctx.guild.id)
        projected = snapshot.count + amount
        await self._reply.send_reply(
            ctx,
            title="Count projection",
            description=f"Current: {snapshot.count}; after {amount}: {projected}",
        )
        return {
            "status": "ok",
            "current_count": snapshot.count,
            "amount": amount,
            "projected_count": projected,
        }

    @{{cookiecutter.cog_name}}_group.command(name="report")
    @llm_tool(
        name="{{cookiecutter.cog_name}}_report",
        description="Report this server's current count in a compact or detailed style.",
        required_group=EMPLOYEE_KEY,
    )
    async def report(
        self,
        ctx: commands.Context,
        style: Annotated[
            str,
            ToolDescription("How much context to include.", enum=_REPORT_STYLES),
        ] = "compact",
    ) -> dict[str, object]:
        """Report the count using the compact or detailed style."""

        if not await self._corridor.require_permission(ctx, EMPLOYEE_KEY):
            return _permission_denied_result()
        if not isinstance(style, str) or style not in _REPORT_STYLES:
            message = "Style must be `compact` or `detailed`."
            await self._reply.send_reply(ctx, title="Count report", description=message)
            return {"status": "error", "error": "invalid_style", "message": message}

        snapshot = await self._service.show(ctx.guild.id)
        description = (
            str(snapshot.count)
            if style == "compact"
            else f"Server {snapshot.guild_id} currently has a count of {snapshot.count}."
        )
        await self._reply.send_reply(ctx, title="Count report", description=description)
        return {
            "status": "ok",
            "guild_id": snapshot.guild_id,
            "count": snapshot.count,
            "style": style,
        }
