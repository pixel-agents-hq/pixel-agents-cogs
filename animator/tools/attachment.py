"""A file animator wants delivered to Discord alongside its final answer.

Deliberately a plain dataclass, not a Pydantic model on `ToolSpec.Output`
directly serialized to the LLM: `ToolLoopService._execute` reads `.data`
(the raw bytes) straight off a tool's returned `Output` via `getattr`,
*before* calling `output.model_dump_json()` -- the JSON text the LLM
actually sees never includes an `Attachment` at all (see
`deliver_assets_tool.py`'s `Output.attachments` field, which is
`Field(exclude=True)`). Keeping this a plain dataclass rather than a
Pydantic field type sidesteps needing a custom bytes-serializer we'd then
have to remember never to use.

Threaded end-to-end unchanged: `ToolLoopResult.attachments` (this cog's own
`application/tool_loop_service.py`) -> `corridor.domain.agent_executor.
GenericAgentExecutor._run_turn` (via `getattr(result, "attachments", ())`)
-> one `a2a.types.Part(raw=..., filename=..., media_type=...)` per
attachment on the A2A response -> `pico/infrastructure/architect_client.py`
collects matching `Part`s back out -> `pico/tools/consult_agent_tool.py`
turns each into a `discord.File` and attaches it to the Discord message
that announces animator's answer."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Attachment:
    filename: str
    media_type: str
    data: bytes


__all__ = ["Attachment"]
