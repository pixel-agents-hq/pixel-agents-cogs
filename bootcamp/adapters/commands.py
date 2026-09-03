"""Discord-facing commands. Thin: translate ctx <-> service calls only.

Creating/removing/editing a custom agent is bot-owner-only -- bot-wide LLM
capability configuration (an arbitrary system prompt, tool access), not
guild content, same rationale telephonepole's own commands document for
registering third-party MCP servers. *Using* an already-created agent
(`ask`) is deliberately not owner-gated at the command-decorator level --
its own permission check lives in `CogBase.run_agent`, against that
specific agent's own `permission_group`, so the top-level `bootcamp_group`
callback below must stay free of an `@commands.is_owner()` check too
(discord.py checks on a Group are inherited by every subcommand, `ask`
included).

Replies go through corridor (this cog's `required_cogs` dependency) rather
than `ctx.send()`/hand-rolled role checks -- except `list`, whose
Components V2 panel is sent via a plain `ctx.send(view=...)`, the same
lint-exempt convention `telephonepole`'s own `agents` command uses
(Components V2 cannot be mixed with an embed/content, so it structurally
cannot honor `ReplyMode`).
"""

from __future__ import annotations

from typing import Any

from redbot.core import commands

from ..application import BootcampService
from .agent_list_panel import AgentListView

# A bot owner may reset an agent's request_timeout_seconds back to "use
# corridor's own default" with any of these literals, case-insensitively --
# accepted by both `create`'s optional positional arg and `requesttimeout`.
_TIMEOUT_DEFAULT_LITERALS = frozenset({"default", "none"})


def _parse_request_timeout(raw: str) -> tuple[float | None, str | None]:
    """Returns `(value, error)` -- exactly one is `None`. `value` is the
    parsed `request_timeout_seconds` (`None` meaning "reset to corridor's
    own default") on success; `error` is a user-facing message on
    failure."""

    if raw.strip().lower() in _TIMEOUT_DEFAULT_LITERALS:
        return None, None
    try:
        value = float(raw)
    except ValueError:
        return None, (
            f"{raw!r} is not a valid request_timeout_seconds -- give a positive number of "
            "seconds, or `default` to use corridor's own default"
        )
    if value <= 0:
        return None, "request_timeout_seconds must be a positive number, or `default`"
    return value, None


class CommandsMixin:
    """Requires `self._service: BootcampService | None`, `self._corridor`,
    `self._reply`, and `self.run_agent(...)` (all provided by CogBase)."""

    _service: BootcampService | None
    _corridor: Any
    _reply: Any

    @commands.hybrid_group(name="bootcamp")
    async def bootcamp_group(self, ctx: commands.Context) -> None:
        """Create and consult custom LLM agents with their own system prompt."""

        if ctx.invoked_subcommand is None:
            await ctx.send_help()

    @bootcamp_group.command(name="create")
    @commands.is_owner()
    async def create(
        self,
        ctx: commands.Context,
        agent_key: str,
        request_timeout_seconds: str = "default",
        *,
        system_prompt: str,
    ) -> None:
        """Create a custom agent, usable by everyone until narrowed with
        `[p]bootcamp permission`. `request_timeout_seconds` is `default`
        (corridor's own default) or a positive number of seconds --
        editable later with `[p]bootcamp requesttimeout`."""

        assert self._service is not None, "bootcamp: cog_load has not completed yet"
        timeout_value, timeout_error = _parse_request_timeout(request_timeout_seconds)
        if timeout_error is not None:
            await self._reply.send_reply(
                ctx, title="Could not create agent", description=timeout_error
            )
            return
        error = await self._service.create_agent(
            agent_key, system_prompt, request_timeout_seconds=timeout_value
        )
        if error is not None:
            await self._reply.send_reply(ctx, title="Could not create agent", description=error)
            return
        await self._reply.send_reply(
            ctx,
            title="Agent created",
            description=(
                f"**{agent_key}** is now registered -- reachable via pico's own "
                f"`consult_{agent_key}` tool and directly with "
                f"`[p]bootcamp ask {agent_key} <prompt>`."
            ),
        )

    @bootcamp_group.command(name="remove")
    @commands.is_owner()
    async def remove(self, ctx: commands.Context, agent_key: str) -> None:
        """Remove a custom agent."""

        assert self._service is not None, "bootcamp: cog_load has not completed yet"
        error = await self._service.remove_agent(agent_key)
        if error is not None:
            await self._reply.send_reply(ctx, title="Could not remove agent", description=error)
            return
        await self._reply.send_reply(
            ctx, title="Agent removed", description=f"**{agent_key}** has been removed."
        )

    @bootcamp_group.command(name="list")
    @commands.is_owner()
    async def list_agents(self, ctx: commands.Context) -> None:
        """Open a Components V2 panel listing every custom agent and its
        full configuration."""

        assert self._service is not None, "bootcamp: cog_load has not completed yet"
        view = await AgentListView.create(self, owner_id=ctx.author.id)
        await ctx.send(view=view)

    @bootcamp_group.command(name="permission")
    @commands.is_owner()
    async def permission(self, ctx: commands.Context, agent_key: str, group_key: str) -> None:
        """Set which corridor permission group gates use of a custom agent,
        both directly and through pico."""

        assert self._service is not None, "bootcamp: cog_load has not completed yet"
        error = await self._service.set_permission_group(agent_key, group_key)
        if error is not None:
            await self._reply.send_reply(ctx, title="Could not update agent", description=error)
            return
        await self._reply.send_reply(
            ctx,
            title="Agent updated",
            description=f"**{agent_key}** now requires the `{group_key}` permission group.",
        )

    @bootcamp_group.command(name="maxtoolcalls")
    @commands.is_owner()
    async def maxtoolcalls(self, ctx: commands.Context, agent_key: str, value: int) -> None:
        """Set a custom agent's per-turn tool-call budget."""

        assert self._service is not None, "bootcamp: cog_load has not completed yet"
        error = await self._service.set_max_tool_calls(agent_key, value)
        if error is not None:
            await self._reply.send_reply(ctx, title="Could not update agent", description=error)
            return
        await self._reply.send_reply(
            ctx,
            title="Agent updated",
            description=f"**{agent_key}**'s max_tool_calls is now {value}.",
        )

    @bootcamp_group.command(name="debuglogging")
    @commands.is_owner()
    async def debuglogging(self, ctx: commands.Context, agent_key: str, value: bool) -> None:
        """Toggle a custom agent's debug-event streaming."""

        assert self._service is not None, "bootcamp: cog_load has not completed yet"
        error = await self._service.set_debug_logging(agent_key, value)
        if error is not None:
            await self._reply.send_reply(ctx, title="Could not update agent", description=error)
            return
        await self._reply.send_reply(
            ctx,
            title="Agent updated",
            description=f"**{agent_key}**'s debug_logging is now {value}.",
        )

    @bootcamp_group.command(name="requesttimeout")
    @commands.is_owner()
    async def requesttimeout(self, ctx: commands.Context, agent_key: str, value: str) -> None:
        """Override a custom agent's LLM request timeout in seconds, or
        reset it to corridor's own default with `default`."""

        assert self._service is not None, "bootcamp: cog_load has not completed yet"
        timeout_value, timeout_error = _parse_request_timeout(value)
        if timeout_error is not None:
            await self._reply.send_reply(
                ctx, title="Could not update agent", description=timeout_error
            )
            return
        error = await self._service.set_request_timeout(agent_key, timeout_value)
        if error is not None:
            await self._reply.send_reply(ctx, title="Could not update agent", description=error)
            return
        display = "default" if timeout_value is None else f"{timeout_value:g}s"
        await self._reply.send_reply(
            ctx,
            title="Agent updated",
            description=f"**{agent_key}**'s request_timeout_seconds is now {display}.",
        )

    @bootcamp_group.command(name="ask")
    async def ask(self, ctx: commands.Context, agent_key: str, *, prompt: str) -> None:
        """Directly consult a custom agent -- gated by that agent's own
        `permission_group`, not by who may create/remove/edit agents."""

        await self.run_agent(ctx, agent_key, prompt)  # type: ignore[attr-defined]
