"""on_message: cheap rule-based short-circuit, then the evaluation gate,
then the bounded tool-calling loop.

The guard clauses at the top of `on_message` (bot author, no guild, guild
not enabled, LLM not configured) return before touching the gate or
building any context at all -- the vast majority of messages that don't
concern Pico cost nothing beyond those checks. `GateService.decide(...)`
itself further short-circuits on reply/mention before ever calling the LLM;
see its module docstring.
"""

from __future__ import annotations

import logging
from typing import Any

import discord
from redbot.core import commands
from redbot.core.bot import Red

from ..application import GateService, ToolLoopService
from ..domain import ConversationContext, GateDecision, HistoryEntry, MessageSnapshot
from ..infrastructure import ArchitectClient, RedPicoRepository
from ..tools.base import ToolSpec
from ..tools.consult_agent_tool import ConsultAgentTool
from ..tools.cross_cog import CrossCogTool
from ..tools.reply_tool import ReplyTool

log = logging.getLogger("red.pico")

HISTORY_LIMIT = 10


class ListenerMixin:
    """Requires `self.bot`, `self._corridor`, `self._repository`,
    `self._gate_service`, `self._tool_loop_service`, `self._architect_client`
    (all provided by CogBase)."""

    bot: Red
    _corridor: Any
    _repository: RedPicoRepository
    _gate_service: GateService
    _tool_loop_service: ToolLoopService
    _architect_client: ArchitectClient

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot:
            return
        guild = message.guild
        if guild is None:
            return
        if not await self._repository.guild_enabled(guild.id):
            return

        llm_settings = await self._corridor.llm_settings()
        if not llm_settings.ready:
            log.debug("pico: LLM not configured (missing key/model), ignoring message")
            return
        settings = await self._repository.global_settings()

        snapshot = _message_snapshot(message, bot_user_id=_bot_user_id(self.bot))
        if snapshot is None:
            return
        history = await _recent_history(message, limit=HISTORY_LIMIT)
        context = ConversationContext(trigger=snapshot, history=history)

        decision = await self._gate_service.decide(
            context,
            base_url=llm_settings.llm_base_url,
            api_key=llm_settings.llm_api_key or "",
            model=llm_settings.llm_model or "",
        )
        if decision is not GateDecision.RESPOND:
            return

        ctx = await self.bot.get_context(message)
        tools: list[ToolSpec] = [
            ReplyTool(self._corridor, ctx, guild_id=guild.id, bot_user_id=_bot_user_id(self.bot))
        ]
        tools.extend(_agent_tools(self._corridor, self._architect_client))
        tools.extend(await _cross_cog_tools(self._corridor, ctx))
        result = await self._tool_loop_service.run(
            base_url=llm_settings.llm_base_url,
            api_key=llm_settings.llm_api_key or "",
            model=llm_settings.llm_model or "",
            system_prompt=settings.system_prompt,
            context=context,
            tools=tools,
            max_tool_calls=settings.max_tool_calls,
        )
        log.debug(
            "pico: tool loop finished (%s, %d call(s))",
            result.stopped_reason,
            result.tool_calls_made,
        )


def _agent_tools(corridor: Any, client: Any) -> list[ToolSpec]:
    """One `consult_<agent_key>` tool per agent currently registered in
    corridor's `AgentDirectoryService`, pulled fresh each turn -- see
    docs/agent-directory-design.md. If corridor has zero registered
    agents (no agent cog loaded, or every one currently unregistered),
    this returns an empty list: no error, no special-cased "not
    configured" branch, since there's no longer a single hardcoded agent
    to be "not configured." One malformed entry's tool-building failure
    is logged and skipped rather than dropping every other agent's tool,
    same convention as `_cross_cog_tools` below."""

    tools: list[ToolSpec] = []
    for agent in corridor.list_agents():
        try:
            tools.append(
                ConsultAgentTool(
                    client,
                    agent_key=agent.agent_key,
                    base_url=agent.card.supported_interfaces[0].url,
                    description=agent.card.description,
                )
            )
        except Exception:
            log.warning(
                "pico: could not build a tool for agent %r, skipping",
                agent.agent_key,
                exc_info=True,
            )
    return tools


async def _cross_cog_tools(corridor: Any, ctx: commands.Context) -> list[ToolSpec]:
    """Tools other cogs (e.g. deskutils) registered into corridor's shared
    tool registry, filtered to what this `ctx` is allowed to invoke
    (corridor resolves an explicit permission group or the Discord
    command's own checks). Each adapted tool closes over this
    same `ctx` -- most registered tools are just `@llm_tool`-decorated
    commands, so calling one invokes the real command callback in this same
    channel, as `ctx.author`. Adapting a misbehaving registration must
    never take down this turn's whole tool loop, so one tool's conversion
    failure is logged and skipped rather than propagated."""

    tools: list[ToolSpec] = []
    for registered in await corridor.list_tools_for(ctx):
        try:
            tools.append(CrossCogTool(registered, ctx))
        except Exception:
            log.warning(
                "pico: could not adapt cross-cog tool %r, skipping",
                registered.name,
                exc_info=True,
            )
    return tools


def _bot_user_id(bot: Red) -> int | None:
    user = bot.user
    return user.id if user is not None else None


def _message_snapshot(
    message: discord.Message, *, bot_user_id: int | None
) -> MessageSnapshot | None:
    guild = message.guild
    if guild is None:
        return None
    mentions_bot = bot_user_id is not None and any(
        user.id == bot_user_id for user in message.mentions
    )
    return MessageSnapshot(
        guild_id=guild.id,
        channel_id=message.channel.id,
        message_id=message.id,
        author_id=message.author.id,
        author_is_bot=message.author.bot,
        content=message.content or "",
        mentions_bot=mentions_bot,
        is_reply_to_bot=_is_reply_to_bot(message, bot_user_id),
    )


def _is_reply_to_bot(message: discord.Message, bot_user_id: int | None) -> bool:
    reference = message.reference
    if reference is None or bot_user_id is None:
        return False
    # `resolved` is only populated when the referenced message was already
    # in discord.py's cache -- an uncached reply is treated as "not a reply
    # to the bot" rather than fetched, keeping this check free of extra
    # Discord API calls.
    author = getattr(reference.resolved, "author", None)
    return author is not None and getattr(author, "id", None) == bot_user_id


async def _recent_history(message: discord.Message, *, limit: int) -> tuple[HistoryEntry, ...]:
    entries: list[HistoryEntry] = []
    async for historical in message.channel.history(limit=limit, before=message):
        entries.append(
            HistoryEntry(
                author_name=historical.author.display_name,
                author_is_bot=historical.author.bot,
                content=_message_text(historical),
            )
        )
    entries.reverse()
    return tuple(entries)


def _message_text(message: discord.Message) -> str:
    """Plain `content` plus a text rendering of any embeds, so embed-only
    messages don't show up as empty history entries. Covers both corridor's
    own EMBED reply mode (see `corridor/application/reply_service.py` --
    the same title/description/fields shape its TEXT mode falls back to)
    and embeds posted by other bots."""

    parts = [message.content] if message.content else []
    for embed in message.embeds:
        if embed.title:
            parts.append(str(embed.title))
        if embed.description:
            parts.append(str(embed.description))
        parts.extend(
            f"**{field.name}:** {field.value}"
            for field in embed.fields
            if field.name or field.value
        )
    return "\n".join(parts)


__all__ = ["ListenerMixin"]
