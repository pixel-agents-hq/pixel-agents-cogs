"""Delegates a sub-task to one currently-registered A2A agent. Replaces the
former hardcoded `ArchitectTool`/`[p]pico architecturl` pair -- pico builds
one `ConsultAgentTool` per entry in `corridor.list_agents()` each turn
instead (`adapters/listener.py`'s `_agent_tools`), so a new agent (architect,
or any future one) becomes available with zero pico-side code changes. See
docs/agent-directory-design.md.

The A2A exchange itself is posted to Discord deterministically, by this
tool's own handler -- not left to the LLM's discretion. `ReplyTool` is no
longer the *only* Discord-send in this cog: it remains the only place the
LLM's own composed words reach Discord, but this tool additionally
announces the outgoing question and the target agent's raw answer as its
own two messages, independent of whatever pico's LLM later says in its
own final reply (which still happens, via `ReplyTool`, exactly as
before). This is a deliberate transparency feature: a Discord user
watching the channel sees the real, unparaphrased A2A conversation, not
just pico's summary of it.

Both announcements carry pico's own bound author identity (via `reply`,
a `ReplySender`) *and*, in the footer, the *consulted* agent's own
identity when it has one (`footer_icon_path`, the same real local `Path`
`RegisteredAgent.avatar_path` carries -- attached as a Discord attachment
the same reliable way `reply`'s own avatar is, not fetched from a URL: the
agent's `a2a.types.AgentCard.icon_url` field points at corridor's shared
A2A listener, which only binds `a2a_host`/`a2a_port` -- 127.0.0.1 by
default -- so Discord's own servers can never fetch it, even though it
works fine for this same process's own agent-to-agent calls. See
docs/reply-identity-design.md section 7) -- distinct identities, visible
on the same message.

If the target agent is unloaded or unreachable at call time, that's both
reported back to the LLM as a tool error *and* announced in the channel --
pico still only ever chooses its own *words* through `ReplyTool`, but this
tool's own announcements are not gated behind that choice.

Alongside those Discord messages, this tool also publishes two
`AgentReplied` events onto corridor's Pub/Sub bus
(`corridor/domain/models.py`), so floorplan's office dashboard shows the
same exchange as activity bubbles: the outgoing question attributed to
pico's own Discord bot identity, the raw answer attributed to the
consulted agent's *genuine* identity (`AgentRef.agent_key` --
see `docs/office-agent-identity-design.md`, the same identity shape
`architect/adapters/cog_base.py`'s `ARCHITECT_AGENT_REF` already
publishes its own presence under). `AgentReplied`, not `AgentToolStarted`,
per that event's own docstring and `docs/corridor-pubsub-design.md`'s
mapping table: `AgentReplied` is the one event every subscriber already
renders as a labeled, auto-clearing activity bubble; `AgentToolStarted`
has no real publisher or clear-lifecycle anywhere in this codebase.
A publish failure here is best-effort, like the announcements themselves
-- it must never fail the tool call or suppress the Discord messages.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, Field

from corridor.domain import AgentRef, AgentReplied, FooterOverride, ReplyField

from ..infrastructure.architect_client import AgentAskResult, ArchitectRequestError

log = logging.getLogger("red.pico")


class ConsultAgentInput(BaseModel):
    prompt: str = Field(description="The task or question to delegate to this agent.")


class ConsultAgentOutput(BaseModel):
    status: str
    answer: str | None = None
    error: str | None = None


class ArchitectAsker(Protocol):
    """The slice of ArchitectClient this tool depends on -- name kept from
    architect's original hardcoded tool; the client itself is generic
    (`ask(base_url=..., text=...)`), not architect-specific."""

    async def ask(self, *, base_url: str, text: str) -> AgentAskResult: ...


class ReplySenderProtocol(Protocol):
    """The slice of `corridor.adapters.reply_sender.ReplySender` this tool
    depends on to announce the A2A exchange -- structurally satisfied by
    the real `ReplySender` every cog now obtains from
    `corridor.reply_sender(...)`."""

    async def send_reply(
        self,
        ctx: object,
        *,
        title: str | None = None,
        description: str | None = None,
        content: str | None = None,
        fields: Sequence[ReplyField] = (),
        footer_override: FooterOverride | None = None,
        footer_icon_path: Path | None = None,
    ) -> object: ...


class CorridorEvents(Protocol):
    """The slice of corridor's cross-cog API this tool depends on beyond
    sending -- publishing `AgentReplied` onto the event bus. Same shape as
    `pico.tools.reply_tool.CorridorEvents`; kept as a separate definition
    rather than a shared import since each tool's Protocol is structural
    and neither module should depend on the other's internals. Deliberately
    a plain `corridor` reference, not `ReplySenderProtocol.publish_event`
    (which forwards to the same place but is unused by every real caller
    today -- see `ReplyTool`, which takes both a `reply` sender and this
    `corridor` reference as two separate constructor arguments)."""

    async def publish_event(self, event: object) -> None: ...


class ConsultAgentTool:
    """One instance per currently-registered agent, built fresh each turn
    by `_agent_tools` from corridor's `AgentDirectoryService.list_agents()`
    entries. `name`/`description` come from that agent's own `AgentCard` --
    `card.description` is written by the registering agent
    (`architect/infrastructure/a2a_server.py`'s `AGENT_DESCRIPTION`, or any
    future agent's own), not hardcoded here.

    Closes over the triggering turn's `ctx` and pico's own bound
    `ReplySender`, both supplied fresh per turn by the listener -- same
    convention `ReplyTool`/`CrossCogTool` already follow, since each
    depends on which message/channel triggered this turn."""

    def __init__(
        self,
        client: ArchitectAsker,
        reply: ReplySenderProtocol,
        ctx: object,
        *,
        agent_key: str,
        base_url: str,
        description: str,
        corridor: CorridorEvents,
        guild_id: int,
        bot_user_id: int | None,
        footer_icon_path: Path | None = None,
    ) -> None:
        self.name = f"consult_{agent_key}"
        self.description = description or f"Delegate a task to {agent_key}."
        self._client = client
        self._reply = reply
        self._ctx = ctx
        self._agent_key = agent_key
        self._base_url = base_url
        self._footer_icon_path = footer_icon_path
        self._footer_override = (
            FooterOverride(name=agent_key, icon_filename=footer_icon_path.name)
            if footer_icon_path is not None
            else None
        )
        self._corridor = corridor
        self._guild_id = guild_id
        self._bot_user_id = bot_user_id

    @property
    def Input(self) -> type[BaseModel]:
        return ConsultAgentInput

    @property
    def Output(self) -> type[BaseModel]:
        return ConsultAgentOutput

    async def handler(self, raw_input: BaseModel) -> BaseModel:
        assert isinstance(raw_input, ConsultAgentInput)
        await self._announce(f"🔧 Asking **{self._agent_key}**: {raw_input.prompt}")
        if self._bot_user_id is not None:
            # Same "no bot login yet" guard `ReplyTool._publish_agent_replied`
            # uses -- there's no pico identity to attribute this to without it.
            await self._publish_agent_replied(
                agent=AgentRef(
                    discord_user_id=self._bot_user_id, guild_id=self._guild_id, is_bot=True
                ),
                summary=f"Asking {self._agent_key}: {raw_input.prompt}",
            )
        try:
            result = await self._client.ask(base_url=self._base_url, text=raw_input.prompt)
        except ArchitectRequestError as exc:
            log.warning("pico: %s failed: %s", self.name, exc)
            await self._announce(f"⚠️ **{self._agent_key}** could not be reached: {exc}")
            return ConsultAgentOutput(status="error", error=str(exc))
        await self._announce(
            f"📩 **{self._agent_key}** replied: {result.answer}",
            fields=_tool_call_fields(result.tool_calls_made),
        )
        await self._publish_agent_replied(
            agent=AgentRef(
                discord_user_id=None, guild_id=None, is_bot=True, agent_key=self._agent_key
            ),
            summary=result.answer,
        )
        return ConsultAgentOutput(status="ok", answer=result.answer)

    async def _announce(self, description: str, *, fields: Sequence[ReplyField] = ()) -> None:
        """Best-effort -- a failure to post the announcement must never
        turn a successful (or already-failed) A2A call into a reported
        tool failure, same convention `ReplyTool._publish_agent_replied`
        already follows for its own secondary side effect."""

        try:
            await self._reply.send_reply(
                self._ctx,
                description=description,
                fields=fields,
                footer_override=self._footer_override,
                footer_icon_path=self._footer_icon_path,
            )
        except Exception:
            log.warning("pico: %s could not announce an A2A exchange", self.name, exc_info=True)

    async def _publish_agent_replied(self, *, agent: AgentRef, summary: str) -> None:
        """Drives floorplan's office dashboard with the same exchange
        `_announce` just posted to Discord -- `AgentReplied`, not
        `AgentToolStarted`, per that event's own docstring and
        docs/corridor-pubsub-design.md's mapping table (see this module's
        own docstring). Best-effort, same convention as `_announce` and
        `ReplyTool._publish_agent_replied` -- a bus failure must never fail
        the tool call or suppress the Discord announcement."""

        try:
            await self._corridor.publish_event(AgentReplied(agent=agent, summary=summary))
        except Exception:
            log.warning(
                "pico: %s could not publish an AgentReplied event", self.name, exc_info=True
            )


def _tool_call_fields(tool_calls_made: int | None) -> Sequence[ReplyField]:
    """Omitted entirely when the consulted agent didn't report a count --
    see `AgentAskResult`'s docstring on why that's a normal case, not an
    error, for any agent that isn't running a bounded tool-calling loop."""

    if tool_calls_made is None:
        return ()
    return (ReplyField("Tool calls", str(tool_calls_made)),)


__all__ = [
    "ArchitectAsker",
    "ConsultAgentInput",
    "ConsultAgentOutput",
    "ConsultAgentTool",
    "CorridorEvents",
    "ReplySenderProtocol",
]
