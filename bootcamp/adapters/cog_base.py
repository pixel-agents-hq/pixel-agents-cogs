"""Dependency composition and lifecycle for the Bootcamp Cog.

Unlike architect/painter -- one singleton agent each, registered once at
`cog_load` -- bootcamp hosts an open-ended, bot-owner-managed set of custom
agents, each registered/unregistered independently at runtime. Corridor's
`AgentDirectoryService`/`CogBase.register_agent` already support this: an
`agent_key` is the only real key, `owner` is just a string tag, and
`unregister_agent_owner("Bootcamp")` removes every agent this cog ever
registered in one call regardless of how many there are (see
docs/agent-directory-design.md and docs/bootcamp-design.md).
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path
from typing import Any

from redbot.core import commands
from redbot.core.bot import Red

from corridor.domain import AgentRef, AgentReplied, RegisteredAgent, ReplyCategory
from corridor.domain.agent_executor import GenericAgentExecutor, build_agent_card

from ..application import BootcampService, ToolLoopService
from ..dependency_loader import ensure_corridor_loaded
from ..domain import CustomAgent
from ..infrastructure import CorridorLLMClient, RedBootcampRepository
from ..tools.agent_tool_server import AgentToolServerTool
from ..tools.base import ToolSpec

log = logging.getLogger("red.bootcamp")

# Conventional path for this cog's own bundled avatar image -- passed to
# corridor.reply_sender() regardless of whether a real file exists here
# yet; existence is checked fresh on every send, so dropping a real image
# at this exact path later needs no code change. See
# docs/reply-identity-design.md. Every custom agent shares this one avatar
# too (RegisteredAgent.avatar_path below) -- a bot-owner-uploaded per-agent
# avatar is out of scope for now (see docs/bootcamp-design.md).
AVATAR_PATH = Path(__file__).resolve().parent.parent / "assets" / "avatar.png"

_AGENT_DESCRIPTION_PROMPT_PREVIEW = 200


def _agent_description(agent: CustomAgent) -> str:
    """Prefers the creator's own `description` (this agent's AgentCard
    description, and so the LLM-facing text pico's `_agent_tools` uses to
    decide whether to consult it -- see `domain/models.py`'s own
    docstring). Falls back to a truncated preview of `system_prompt` only
    when no explicit description was set, so an agent created before this
    field existed (or with it left blank) keeps a usable, non-empty
    description rather than an empty string."""

    if agent.description:
        return agent.description
    prompt = agent.system_prompt.strip()
    if len(prompt) > _AGENT_DESCRIPTION_PROMPT_PREVIEW:
        prompt = prompt[:_AGENT_DESCRIPTION_PROMPT_PREVIEW] + "..."
    return f"A custom agent created via [p]bootcamp. System prompt: {prompt}"


async def _mcp_tools(corridor: Any, agent_key: str) -> list[ToolSpec]:
    """Adapt the MCP tools currently enabled for this one custom agent --
    fetched fresh on every turn (never cached), so a bot owner flipping a
    server's per-agent toggle in `[p]telephonepole agents`/
    `[p]suggestionbox agents` takes effect on that agent's very next turn.
    See docs/telephonepole-design.md and docs/suggestionbox-design.md."""

    tools: list[ToolSpec] = []
    for tool in await corridor.list_agent_tools_for(agent_key):
        try:
            tools.append(AgentToolServerTool(tool))
        except Exception:
            log.warning(
                "bootcamp: could not adapt MCP tool %r for %s, skipping",
                getattr(tool, "name", "?"),
                agent_key,
                exc_info=True,
            )
    return tools


class CorridorAgentRegistrar:
    """The only place in this cog that imports `corridor.domain.
    RegisteredAgent`/`corridor.domain.agent_executor` or calls
    `corridor.register_agent`/`unregister_agent` -- same "adapter is the
    only corridor-aware layer" split telephonepole's own
    `CorridorMcpRegistrar` documents for its corridor integration. Every
    agent this cog registers shares `owner="Bootcamp"`, so `cog_unload`
    can drop them all at once via `unregister_agent_owner`."""

    def __init__(
        self,
        corridor: Any,
        *,
        repository: RedBootcampRepository,
        tool_loop: ToolLoopService,
        publish_activity: Callable[[str, str], Awaitable[None]],
    ) -> None:
        self._corridor = corridor
        self._repository = repository
        self._tool_loop = tool_loop
        self._publish_activity = publish_activity

    async def register(self, agent: CustomAgent) -> str | None:
        """Builds this agent's `AgentCard`/`GenericAgentExecutor` fresh
        every call (cheap -- no network round-trip) and registers it with
        corridor. Corridor's own `register_agent` raises `ValueError` on a
        genuine cross-owner `agent_key` collision, which propagates
        straight to `BootcampService`'s own `except ValueError` -- there is
        no other failure mode here (unlike telephonepole's MCP servers,
        registering an agent never opens a network connection)."""

        card = build_agent_card(
            name=agent.agent_key,
            description=_agent_description(agent),
            version="1.0.0",
            tools=(),
            tag=agent.agent_key,
        )
        executor = GenericAgentExecutor(
            agent_name=agent.agent_key,
            logger=log,
            tool_loop=self._tool_loop,
            tools=(),
            settings=lambda: self._settings_for(agent.agent_key),
            llm_settings=self._corridor.llm_settings,
            publish_activity=lambda summary: self._publish_activity(agent.agent_key, summary),
            mcp_tools=lambda: _mcp_tools(self._corridor, agent.agent_key),
        )
        await self._corridor.register_agent(
            RegisteredAgent(
                agent_key=agent.agent_key,
                card=card,
                executor=executor,
                avatar_path=AVATAR_PATH,
                required_permission_group=agent.permission_group,
            ),
            owner="Bootcamp",
        )
        return None

    async def unregister(self, agent_key: str) -> None:
        await self._corridor.unregister_agent(agent_key)

    async def _settings_for(self, agent_key: str) -> CustomAgent:
        """Read fresh every turn, matching `AgentToolServerRegistry`'s own
        "never cache a live toggle" convention above -- a
        `[p]bootcamp maxtoolcalls`/`debuglogging` edit takes effect on that
        agent's very next turn, no cog reload or re-registration needed
        (unlike `permission_group`, which corridor's directory stores in a
        registration-time snapshot -- see `BootcampService.
        set_permission_group`'s own docstring)."""

        agent = await self._repository.get_agent(agent_key)
        if agent is None:
            # Should be unreachable: unregister_agent tears down this
            # agent's executor in the same call that deletes it from
            # Config. Raising surfaces as this turn's own "internal error"
            # failure (GenericAgentExecutor's outer except Exception),
            # rather than a confusing AttributeError deeper in the loop.
            raise RuntimeError(f"bootcamp: no persisted settings for agent {agent_key!r}")
        return agent


class CogBase:
    """Wire services once and own resources spanning the Cog lifetime."""

    bot: Red
    config: Any

    def __init__(self, bot: Red) -> None:
        self.bot = bot
        self._repository = RedBootcampRepository.create(self)
        self.config = self._repository.config
        self._corridor: Any = None
        self._reply: Any = None
        self._service: BootcampService | None = None

        llm = CorridorLLMClient(lambda: self._corridor)
        self._tool_loop_service = ToolLoopService(llm)

    async def cog_load(self) -> None:
        """`required_cogs` in `info.json` is only a Downloader install
        hint -- Red does not auto-load a dependency at runtime just
        because it's declared there, so `ensure_corridor_loaded()` pulls
        corridor back in if it was unloaded independently. Also
        re-registers every persisted custom agent with corridor, since
        corridor's in-memory `AgentDirectoryService` does not survive a
        bot restart even though this cog's own Config does."""

        self._corridor = await ensure_corridor_loaded(self.bot)
        # So unloading corridor cascades to unload this cog too, instead of
        # leaving it running with a stale corridor reference.
        self._corridor.register_dependent("bootcamp")
        # Bound once, reused at every reply call site instead of repeating
        # this cog's owner name as an argument everywhere -- see
        # docs/reply-identity-design.md.
        self._reply = self._corridor.reply_sender(
            owner="Bootcamp", avatar_path=AVATAR_PATH, category=ReplyCategory.AGENT
        )
        registrar = CorridorAgentRegistrar(
            self._corridor,
            repository=self._repository,
            tool_loop=self._tool_loop_service,
            publish_activity=self._publish_activity,
        )
        self._service = BootcampService(self._repository, registrar=registrar)
        errors = await self._service.restore_all()
        if errors:
            await self._notify_owners_restore_failed(errors)

    async def cog_unload(self) -> None:
        if self._corridor is not None:
            await self._corridor.unregister_agent_owner("Bootcamp")
            self._corridor.unregister_dependent("bootcamp")

    async def _notify_owners_restore_failed(self, errors: dict[str, str]) -> None:
        """Best-effort DM -- must never raise: a missing/unreachable owner
        DM is not a reason to fail this cog's own load."""

        detail = "; ".join(f"{key}: {error}" for key, error in errors.items())
        message = (
            f"⚠️ bootcamp could not re-register {len(errors)} custom agent(s) on load "
            f"({detail}). bootcamp is still loaded and its Discord commands work, but "
            "neither pico nor `[p]bootcamp ask` can reach those agents until this is "
            "fixed -- see `[p]bootcamp list` and retry once the issue is resolved."
        )
        try:
            await self.bot.send_to_owners(message)
        except Exception:
            log.exception("bootcamp: could not notify owners about a failed agent restore")

    async def _publish_activity(self, agent_key: str, summary: str) -> None:
        """Per-instance version of architect/painter's own
        `_publish_activity` -- they hardcode one module-level `AgentRef`
        because each is a singleton; bootcamp takes `agent_key` as a
        parameter and builds one fresh per call, since it hosts an
        open-ended number of agents."""

        try:
            await self._corridor.publish_event(
                AgentReplied(
                    agent=AgentRef(
                        discord_user_id=None, guild_id=None, is_bot=True, agent_key=agent_key
                    ),
                    summary=summary,
                )
            )
        except Exception:
            log.exception("bootcamp: failed to publish tool/thinking activity for %s", agent_key)

    async def run_agent(self, ctx: commands.Context, agent_key: str, prompt: str) -> str | None:
        """Direct-invoke path for `[p]bootcamp ask <agent_key> <prompt>` --
        runs the same bounded tool loop `GenericAgentExecutor` runs for an
        A2A consultation, in-process, with no A2A round-trip: a cog
        invoking its own agent has no cog boundary to cross, unlike pico's
        `ConsultAgentTool` (or architect/painter consulting each other),
        which are genuinely crossing one. Checks `agent.permission_group`
        via corridor's existing `require_permission` -- that method already
        sends the "no permission" reply itself on failure.

        Returns the agent's final answer, or `None` if the agent doesn't
        exist, permission was denied, or it couldn't produce an answer --
        each case has already sent its own explanation to `ctx` by the
        time this returns."""

        assert self._service is not None, "bootcamp: cog_load has not completed yet"
        agent = await self._service.get_agent(agent_key)
        if agent is None:
            await self._reply.send_reply(
                ctx,
                title="Unknown agent",
                description=f"No custom agent named `{agent_key}` exists. See `[p]bootcamp list`.",
            )
            return None
        if not await self._corridor.require_permission(ctx, agent.permission_group):
            return None

        llm_settings = await self._corridor.llm_settings()
        if not llm_settings.ready:
            await self._reply.send_reply(
                ctx,
                title=agent_key,
                description="Bootcamp's shared LLM connection is not configured yet.",
            )
            return None

        tools: Sequence[ToolSpec] = await _mcp_tools(self._corridor, agent_key)
        result = await self._tool_loop_service.run(
            base_url=llm_settings.llm_base_url,
            api_key=llm_settings.llm_api_key or "",
            model=llm_settings.llm_model or "",
            system_prompt=agent.system_prompt,
            user_input=prompt,
            tools=tools,
            max_tool_calls=agent.max_tool_calls,
            debug=agent.debug_logging,
            on_activity=lambda summary: self._publish_activity(agent_key, summary),
            request_timeout_seconds=agent.request_timeout_seconds,
        )
        if result.stopped_reason != "final_text" or result.text is None:
            await self._reply.send_reply(
                ctx,
                title=agent_key,
                description=f"{agent_key} could not produce an answer ({result.stopped_reason}).",
            )
            return None
        await self._reply.send_reply(ctx, title=agent_key, description=result.text)
        return result.text


__all__ = ["AVATAR_PATH", "CogBase", "CorridorAgentRegistrar"]
