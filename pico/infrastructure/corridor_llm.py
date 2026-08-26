"""Lazily resolves corridor's shared `LiteLLMClient` at call time.

`GateService`/`ToolLoopService` are constructed in `CogBase.__init__`, but
corridor itself is only resolved later, in `cog_load()` (it requires an
`await`). This proxy lets both services hold a stable reference from the
start -- satisfying `GateLLM`/`ToolLLM` structurally, exactly like the
`LiteLLMClient` they used to hold directly -- while the real lookup happens
on each call, by which point `cog_load()` has always already run.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any, cast

from corridor.infrastructure.llm_client import ChatCompletionResponse, ChatMessage, ToolSpecWire


class CorridorLLMClient:
    def __init__(self, corridor_ref: Callable[[], Any]) -> None:
        self._corridor_ref = corridor_ref

    async def complete(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        messages: Sequence[ChatMessage],
        tools: Sequence[ToolSpecWire] | None = None,
        tool_choice: str | None = None,
    ) -> ChatCompletionResponse:
        corridor = self._corridor_ref()
        response = await corridor.llm_client().complete(
            base_url=base_url,
            api_key=api_key,
            model=model,
            messages=messages,
            tools=tools,
            tool_choice=tool_choice,
        )
        return cast(ChatCompletionResponse, response)


__all__ = ["CorridorLLMClient"]
