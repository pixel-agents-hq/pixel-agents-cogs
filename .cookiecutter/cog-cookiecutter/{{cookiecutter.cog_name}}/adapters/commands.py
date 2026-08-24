"""Discord-facing commands. Thin: translate ctx <-> service calls only.

Replies and permission checks go through corridor (this cog's required_cogs
dependency) rather than ctx.send()/hand-rolled role checks, so this cog
automatically respects whatever reply style and permission groups the guild
has already configured for every other cog.
"""

from __future__ import annotations

from typing import Any

from redbot.core import commands

from corridor.adapters import llm_tool

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
    # @llm_tool marks this callback as a cross-cog LLM tool: any cog with
    # `pico` loaded can then have its LLM call this exact command directly
    # (same permission check, same reply) instead of a user typing it by
    # hand -- see docs/corridor-tool-registry-design.md. Delete this
    # decorator (and the now-unused `llm_tool` import above) if this
    # command shouldn't be LLM-callable; it's entirely optional per
    # command, not something every command needs.
    #
    # `parameters`'s JSON Schema is inferred from this callback's own
    # signature -- `bump` takes none beyond `self`/`ctx`, so there's
    # nothing to describe here. A command with its own parameters (like
    # deskutils' `time_command`) should give each one a description an LLM
    # would need by wrapping its type in `typing.Annotated`, right in the
    # signature -- e.g. `count: Annotated[int, "How many to bump by."]`.
    # `@llm_tool` strips `Annotated` back down to the bare type before
    # discord.py's own command construction ever sees it, so this is safe
    # to write directly on a real command parameter -- see that
    # decorator's own docstring for the full story.
    @llm_tool(
        name="{{cookiecutter.cog_name}}_bump",
        description="Increment this server's count by one.",
        required_group="keyholder",
    )
    async def bump(self, ctx: commands.Context) -> None:
        """Increment this server's count by one. Requires the keyholder tier."""

        if not await self._corridor.require_permission(ctx, "keyholder"):
            return
        snapshot = await self._service.bump(ctx.guild.id)
        await self._corridor.send_reply(ctx, title="Count", description=f"Now: {snapshot.count}")
