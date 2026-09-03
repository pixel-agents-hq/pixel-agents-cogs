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
"""

from __future__ import annotations

from typing import Any

from redbot.core import commands

from ..application import BootcampService


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
    async def create(self, ctx: commands.Context, agent_key: str, *, system_prompt: str) -> None:
        """Create a custom agent, usable by everyone until narrowed with
        `[p]bootcamp permission`."""

        assert self._service is not None, "bootcamp: cog_load has not completed yet"
        error = await self._service.create_agent(agent_key, system_prompt)
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
        """List every custom agent, its permission group, and its settings."""

        assert self._service is not None, "bootcamp: cog_load has not completed yet"
        agents = await self._service.list_agents()
        if not agents:
            await self._reply.send_reply(
                ctx, title="Custom agents", description="No custom agents exist yet."
            )
            return
        description = "\n".join(
            f"**{agent.agent_key}** -- permission: `{agent.permission_group}`, "
            f"max_tool_calls: {agent.max_tool_calls}, debug_logging: {agent.debug_logging}"
            for agent in agents
        )
        await self._reply.send_reply(ctx, title="Custom agents", description=description)

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

    @bootcamp_group.command(name="ask")
    async def ask(self, ctx: commands.Context, agent_key: str, *, prompt: str) -> None:
        """Directly consult a custom agent -- gated by that agent's own
        `permission_group`, not by who may create/remove/edit agents."""

        await self.run_agent(ctx, agent_key, prompt)  # type: ignore[attr-defined]
