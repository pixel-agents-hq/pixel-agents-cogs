"""Adapts corridor's `AgentToolServerRegistry` entries
(`corridor.domain.RegisteredTool`, fetched via `corridor.list_agent_tools_for
("bootcamp")`) into bootcamp's own `ToolSpec` Protocol.

Structurally identical to `pico/tools/cross_cog.py`'s `CrossCogTool`, but
implementing bootcamp's *own* `ToolSpec` (`bootcamp/tools/base.py`)
rather than pico's -- a deliberate duplication, matching that module's own
documented precedent that bootcamp's `ToolSpec` is "a deliberate parallel
copy of pico/tools/base.py's ToolSpec ... not a shared import." See
docs/suggestionbox-design.md §6.

`RegisteredTool.handler` takes an opaque per-invocation `ctx: object` --
there is no Discord ctx for an A2A call, so this passes `None`; every
handler reachable through `AgentToolServerRegistry` today (suggestionbox's
MCP-backed tools) ignores its `ctx` argument entirely for exactly this
reason (see `corridor/application/agent_tool_server_registry.py`).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

from corridor.domain import RegisteredTool


class _PassthroughOutput(BaseModel):
    """Echoes an arbitrary JSON object back out via model_dump_json() --
    shared by every AgentToolServerTool since none of them advertise an
    output schema to the LLM (only `parameters`/Input goes on the wire),
    same convention `pico.tools.cross_cog.CrossCogTool` already uses."""

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
    """Wraps one corridor `RegisteredTool` as a bootcamp `ToolSpec`."""

    def __init__(self, tool: RegisteredTool) -> None:
        self._tool = tool
        self.name = tool.name
        self.description = tool.description
        # Eager, not lazy inside the Input property: a malformed
        # `parameters` must fail here, at adapt time -- where the per-entry
        # try/except building bootcamp's tool list can log and skip just
        # this one tool -- rather than later inside ToolLoopService.run(),
        # which would take down the whole turn. Same reasoning CrossCogTool
        # itself documents.
        parameters = dict(tool.parameters)
        self._input_cls = _passthrough_input_model(tool.name, parameters)

    @property
    def Input(self) -> type[BaseModel]:
        return self._input_cls

    @property
    def Output(self) -> type[BaseModel]:
        return _PassthroughOutput

    async def handler(self, raw_input: BaseModel) -> BaseModel:
        result = await self._tool.handler(None, raw_input.model_dump())
        return _PassthroughOutput.model_validate(dict(result))


__all__ = ["AgentToolServerTool"]
