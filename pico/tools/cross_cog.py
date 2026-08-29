"""Adapts corridor's cross-cog tool registry (`corridor.domain.RegisteredTool`)
into pico's own `ToolSpec` Protocol, so the tool-calling loop can invoke a
tool registered by another cog (e.g. deskutils) exactly like a pico-native
one (`reply_tool.ReplyTool`) -- without `ToolLoopService`/`ToolSpec` ever
needing to change, and without the registering cog needing a pydantic
dependency just to participate. See docs/corridor-tool-registry-design.md.

`RegisteredTool.parameters` is already the exact OpenAI-style JSON Schema
dict pico's wire format needs (`ToolLoopService._wire_spec` calls
`tool.Input.model_json_schema()`) -- the synthetic `Input` class below
returns it verbatim from an overridden `model_json_schema()` classmethod
rather than trying to reconstruct an equivalent schema from typed pydantic
fields, so there is no lossy type-mapping step and no schema shape this
adapter can't represent. Argument *validation* stays exactly where it
always was: the registering cog's own handler, same as for its Discord
command -- `extra="allow"` on both synthetic models means pydantic here
only ever passes JSON objects through unmodified.

Also publishes `AgentReplied` after a successful call, same convention
`ReplyTool`/`ConsultAgentTool` already follow -- pico is the one actually
calling the tool (the registering cog, e.g. deskutils, only supplied the
handler), so pico is the one that publishes, exactly the way it already
publishes on their behalf for `send_reply`/A2A consults. corridor
(floorplan) and architect's own dashboard render whatever the bus
delivers; this adapter has no canvas-facing opinion of its own. See
docs/corridor-pubsub-design.md.
"""

from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel, ConfigDict

from corridor.domain import AgentRef, AgentReplied, RegisteredTool

log = logging.getLogger("red.pico")


class _PassthroughOutput(BaseModel):
    """Echoes an arbitrary JSON object back out via model_dump_json() --
    shared by every CrossCogTool since none of them advertise an output
    schema to the LLM (only `parameters`/Input goes on the wire)."""

    model_config = ConfigDict(extra="allow")


def _passthrough_input_model(tool_name: str, parameters: dict[str, Any]) -> type[BaseModel]:
    class _Input(BaseModel):
        model_config = ConfigDict(extra="allow")

        @classmethod
        def model_json_schema(cls, *args: Any, **kwargs: Any) -> dict[str, Any]:
            return dict(parameters)

    _Input.__name__ = f"{tool_name}_Input"
    return _Input


class CrossCogTool:
    """Wraps one corridor `RegisteredTool` as a pico `ToolSpec`, closing
    over the triggering turn's `ctx` -- most registered tools are just
    `@llm_tool`-decorated commands (see
    `corridor/adapters/llm_tool_registration.py`), whose handler invokes
    the real command callback with this same `ctx`, exactly as if a human
    had typed the command in this same channel.

    `corridor`/`guild_id`/`bot_user_id` are the same values `ReplyTool` is
    built with (see `pico/adapters/listener.py`) -- needed here only to
    publish `AgentReplied` after a successful call, attributing it to
    pico's own Discord identity the same way `ReplyTool` does."""

    def __init__(
        self,
        tool: RegisteredTool,
        ctx: object,
        *,
        corridor: Any,
        guild_id: int,
        bot_user_id: int | None,
    ) -> None:
        self._tool = tool
        self._ctx = ctx
        self._corridor = corridor
        self._guild_id = guild_id
        self._bot_user_id = bot_user_id
        self.name = tool.name
        self.description = tool.description
        # Eager, not lazy inside the Input property: a malformed
        # `parameters` (not actually mapping-shaped) must fail here, at
        # adapt time -- where pico/adapters/listener.py's per-tool
        # try/except can log and skip just that one tool -- rather than
        # later inside ToolLoopService.run(), which would take down the
        # whole turn.
        parameters = dict(tool.parameters)
        self._input_cls = _passthrough_input_model(tool.name, parameters)

    @property
    def Input(self) -> type[BaseModel]:
        return self._input_cls

    @property
    def Output(self) -> type[BaseModel]:
        return _PassthroughOutput

    async def handler(self, raw_input: BaseModel) -> BaseModel:
        result = await self._tool.handler(self._ctx, raw_input.model_dump())
        await self._publish_agent_replied()
        return _PassthroughOutput.model_validate(dict(result))

    async def _publish_agent_replied(self) -> None:
        # Best-effort, same convention as ReplyTool._publish_agent_replied
        # -- the tool call already succeeded by this point, so a bus
        # failure here must never turn that into a reported tool failure.
        if self._bot_user_id is None:
            return
        try:
            await self._corridor.publish_event(
                AgentReplied(
                    agent=AgentRef(
                        discord_user_id=self._bot_user_id, guild_id=self._guild_id, is_bot=True
                    ),
                    summary=f"using tool {self.name}",
                )
            )
        except Exception:
            log.warning(
                "pico: %s could not publish an AgentReplied event", self.name, exc_info=True
            )


__all__ = ["CrossCogTool"]
