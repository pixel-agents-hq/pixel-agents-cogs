"""Adapts corridor's `AgentToolServerRegistry` entries
(`corridor.domain.RegisteredTool`, fetched via `corridor.list_agent_tools_for
("animator")`) into animator's own `ToolSpec` Protocol.

A deliberate parallel copy of `painter/tools/agent_tool_server.py` --
implementing animator's *own* `ToolSpec` (`animator/tools/base.py`) rather
than painter's, matching that module's own documented precedent. See
docs/suggestionbox-design.md §6.

`RegisteredTool.handler` takes an opaque per-invocation `ctx: object` --
there is no Discord ctx for an A2A call, so this passes `None`; every
handler reachable through `AgentToolServerRegistry` today ignores its
`ctx` argument entirely for exactly this reason.

Animator is the first agent whose entire native tool set (besides its own
`deliver_pixel_agents_assets`) comes through this bridge -- see
`adapters/cog_base.py`'s `_mcp_tools` and `README.md` for the
`[p]telephonepole add`/`[p]telephonepole agents` setup this depends on.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

from corridor.domain import McpCallOptions, RegisteredTool


class _PassthroughOutput(BaseModel):
    """Echoes an arbitrary JSON object back out via model_dump_json() --
    shared by every AgentToolServerTool since none of them advertise an
    output schema to the LLM (only `parameters`/Input goes on the wire)."""

    model_config = ConfigDict(extra="allow")


def _passthrough_input_model(tool_name: str, parameters: dict[str, Any]) -> type[BaseModel]:
    class _Input(BaseModel):
        model_config = ConfigDict(extra="allow")

        @classmethod
        def model_json_schema(cls, *args: Any, **kwargs: Any) -> dict[str, Any]:
            return dict(parameters)

    _Input.__name__ = f"{tool_name}_Input"
    return _Input


class AgentToolServerTool:
    """Wraps one corridor `RegisteredTool` as an animator `ToolSpec`.

    `read_timeout_seconds`, when given, is passed down as an
    `McpCallOptions` in place of the `ctx=None` every other
    `AgentToolServerTool` copy (architect's/painter's/bootcamp's, pico's
    `CrossCogTool`) always passes -- see that type's own docstring. Set
    from animator's `[p]animator readtimeout`
    (`GlobalSettings.read_timeout_seconds`): pixel-art-mcp's own tool calls
    can include a genuinely blocking `wait_for_job`, which needs a real
    per-call network timeout far longer (or, per-call, shorter) than
    `McpClientPool`'s own default -- see that method's docstring for why an
    `httpx`-level override alone would not have been enough here."""

    def __init__(self, tool: RegisteredTool, *, read_timeout_seconds: float | None = None) -> None:
        self._tool = tool
        self.name = tool.name
        self.description = tool.description
        self._read_timeout_seconds = read_timeout_seconds
        # Eager, not lazy inside the Input property: a malformed
        # `parameters` must fail here, at adapt time -- where the per-entry
        # try/except building animator's tool list can log and skip just
        # this one tool -- rather than later inside ToolLoopService.run(),
        # which would take down the whole turn.
        parameters = dict(tool.parameters)
        self._input_cls = _passthrough_input_model(tool.name, parameters)

    @property
    def Input(self) -> type[BaseModel]:
        return self._input_cls

    @property
    def Output(self) -> type[BaseModel]:
        return _PassthroughOutput

    async def handler(self, raw_input: BaseModel) -> BaseModel:
        ctx = (
            McpCallOptions(timeout_seconds=self._read_timeout_seconds)
            if self._read_timeout_seconds is not None
            else None
        )
        result = await self._tool.handler(ctx, raw_input.model_dump())
        return _PassthroughOutput.model_validate(dict(result))


__all__ = ["AgentToolServerTool"]
