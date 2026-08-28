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
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, Field

from corridor.domain import FooterOverride, ReplyField

from ..infrastructure.architect_client import ArchitectRequestError

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

    async def ask(self, *, base_url: str, text: str) -> str: ...


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

    @property
    def Input(self) -> type[BaseModel]:
        return ConsultAgentInput

    @property
    def Output(self) -> type[BaseModel]:
        return ConsultAgentOutput

    async def handler(self, raw_input: BaseModel) -> BaseModel:
        assert isinstance(raw_input, ConsultAgentInput)
        await self._announce(f"🔧 Asking **{self._agent_key}**: {raw_input.prompt}")
        try:
            answer = await self._client.ask(base_url=self._base_url, text=raw_input.prompt)
        except ArchitectRequestError as exc:
            log.warning("pico: %s failed: %s", self.name, exc)
            await self._announce(f"⚠️ **{self._agent_key}** could not be reached: {exc}")
            return ConsultAgentOutput(status="error", error=str(exc))
        await self._announce(f"📩 **{self._agent_key}** replied: {answer}")
        return ConsultAgentOutput(status="ok", answer=answer)

    async def _announce(self, description: str) -> None:
        """Best-effort -- a failure to post the announcement must never
        turn a successful (or already-failed) A2A call into a reported
        tool failure, same convention `ReplyTool._publish_agent_replied`
        already follows for its own secondary side effect."""

        try:
            await self._reply.send_reply(
                self._ctx,
                description=description,
                footer_override=self._footer_override,
                footer_icon_path=self._footer_icon_path,
            )
        except Exception:
            log.warning("pico: %s could not announce an A2A exchange", self.name, exc_info=True)


__all__ = [
    "ArchitectAsker",
    "ConsultAgentInput",
    "ConsultAgentOutput",
    "ConsultAgentTool",
    "ReplySenderProtocol",
]
