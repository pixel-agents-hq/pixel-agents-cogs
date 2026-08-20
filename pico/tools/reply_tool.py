"""The one iteration-1 tool: wraps `corridor.send_reply`.

This is the only Discord-send in the whole cog. `ToolLoopService` never
touches Discord directly -- the only path from an LLM tool call to a
message actually appearing in the channel is this handler, which keeps the
cog compliant with `contracts/discord_replies/lint_reply_channel.py`'s
"always through corridor" rule.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Protocol

from pydantic import BaseModel, Field

from corridor.domain import ReplyField

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


class CorridorReply(Protocol):
    """The slice of corridor's cross-cog API this tool depends on."""

    async def send_reply(
        self,
        ctx: object,
        *,
        title: str | None = None,
        description: str | None = None,
        content: str | None = None,
        fields: Sequence[ReplyField] = (),
    ) -> SentMessage: ...


class ReplyTool:
    """Handler closes over the triggering `ctx` and the corridor reference
    -- both supplied fresh per turn by the listener, since each depends on
    which message/channel triggered this turn."""

    name = "send_reply"
    description = (
        "Send a reply in the channel the triggering message was posted in. This is "
        "the only way to say anything -- there is no other way to reach Discord."
    )
    Input = ReplyInput
    Output = ReplyOutput

    def __init__(self, corridor: CorridorReply, ctx: object) -> None:
        self._corridor = corridor
        self._ctx = ctx

    async def handler(self, raw_input: BaseModel) -> BaseModel:
        assert isinstance(raw_input, ReplyInput)
        fields = [ReplyField(f.name, f.value, f.inline) for f in raw_input.fields]
        try:
            message = await self._corridor.send_reply(
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
        return ReplyOutput(sent=True, message_id=message.id, error=None)


__all__ = ["CorridorReply", "ReplyFieldInput", "ReplyInput", "ReplyOutput", "ReplyTool"]
