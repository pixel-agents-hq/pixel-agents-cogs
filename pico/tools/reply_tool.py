"""The one iteration-1 tool: wraps `corridor.send_reply`.

`ToolLoopService` never touches Discord directly -- every message that
reaches a channel is sent by some tool's own handler calling
`corridor.send_reply`, keeping the cog compliant with
`contracts/discord_replies/lint_reply_channel.py`'s "always through
corridor" rule. This is the only tool through which pico's own LLM
*chooses its own words* to say something -- `ConsultAgentTool`
(`tools/consult_agent_tool.py`) also sends, but deterministically
announces the raw A2A exchange, not anything the LLM composed itself.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Protocol

from pydantic import BaseModel, Field

from corridor.domain import AgentRef, ReplyField

from .activity import publish_agent_replied

log = logging.getLogger("red.pico")


class ReplyFieldInput(BaseModel):
    """Mirrors `corridor.domain.ReplyField` (name/value/inline) at the LLM
    wire boundary -- kept separate so this module's schema doesn't leak a
    corridor-owned type into the tool-call JSON schema directly."""

    name: str
    value: str
    inline: bool = True


class ReplyInput(BaseModel):
    """Mirrors `corridor.send_reply`'s kwargs 1:1."""

    content: str | None = None
    title: str | None = None
    description: str | None = None
    fields: list[ReplyFieldInput] = Field(default_factory=list)


class ReplyOutput(BaseModel):
    sent: bool
    message_id: int | None = None
    error: str | None = None


class SentMessage(Protocol):
    """The minimal discord.Message surface this tool reads back."""

    id: int


class ReplySenderProtocol(Protocol):
    """The slice of `corridor.adapters.reply_sender.ReplySender` this tool
    depends on -- structurally satisfied by the real `ReplySender` every
    cog now obtains from `corridor.reply_sender(...)`. Kept separate from
    `CorridorEvents` below rather than one wider protocol: an identity-
    bound sender and the plain corridor event bus are different
    capabilities, and this tool is handed both explicitly by the listener
    rather than one object trying to be both."""

    async def send_reply(
        self,
        ctx: object,
        *,
        title: str | None = None,
        description: str | None = None,
        content: str | None = None,
        fields: Sequence[ReplyField] = (),
    ) -> SentMessage: ...


class CorridorEvents(Protocol):
    """The slice of corridor's cross-cog API this tool depends on beyond
    sending -- publishing `AgentReplied` onto the event bus."""

    async def publish_event(self, event: object) -> None: ...


class ReplyTool:
    """Handler closes over the triggering `ctx`, pico's own bound
    `ReplySender`, and the plain corridor reference (for `publish_event`
    only) -- all supplied fresh per turn by the listener, since each
    depends on which message/channel triggered this turn."""

    name = "send_reply"
    description = (
        "Send a reply in the channel the triggering message was posted in. This is "
        "the only way to say anything -- there is no other way to reach Discord."
    )
    Input = ReplyInput
    Output = ReplyOutput

    def __init__(
        self,
        reply: ReplySenderProtocol,
        corridor: CorridorEvents,
        ctx: object,
        *,
        guild_id: int,
        bot_user_id: int | None,
    ) -> None:
        self._reply = reply
        self._corridor = corridor
        self._ctx = ctx
        self._guild_id = guild_id
        self._bot_user_id = bot_user_id

    async def handler(self, raw_input: BaseModel) -> BaseModel:
        assert isinstance(raw_input, ReplyInput)
        fields = [ReplyField(f.name, f.value, f.inline) for f in raw_input.fields]
        try:
            message = await self._reply.send_reply(
                self._ctx,
                title=raw_input.title,
                description=raw_input.description,
                content=raw_input.content,
                fields=fields,
            )
        except Exception as exc:
            # Reported back to the LLM as a failed tool result rather than
            # raising into ToolLoopService -- a bad reply shouldn't crash
            # the whole turn.
            log.warning("pico: reply tool failed to send: %s", exc)
            return ReplyOutput(sent=False, message_id=None, error=str(exc))
        await self._publish_agent_replied(raw_input)
        return ReplyOutput(sent=True, message_id=message.id, error=None)

    async def _publish_agent_replied(self, raw_input: ReplyInput) -> None:
        # Separate from send_reply's own try/except above -- the message
        # was already sent successfully by this point, so a corridor bus
        # failure here must never turn that into a reported tool failure.
        if self._bot_user_id is None:
            return
        summary = raw_input.content or raw_input.description or raw_input.title or ""
        await publish_agent_replied(
            self._corridor,
            AgentRef(discord_user_id=self._bot_user_id, guild_id=self._guild_id, is_bot=True),
            summary,
            tool_name=self.name,
        )


__all__ = [
    "CorridorEvents",
    "ReplyFieldInput",
    "ReplyInput",
    "ReplyOutput",
    "ReplySenderProtocol",
    "ReplyTool",
]
