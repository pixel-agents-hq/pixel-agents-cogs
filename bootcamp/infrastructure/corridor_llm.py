"""Lazily resolves corridor's shared `LiteLLMClient` at call time.

A parallel copy of `pico/infrastructure/corridor_llm.py`/
`architect/infrastructure/corridor_llm.py` -- see docs/architect-design.md
on why each LLM-backed cog holds its own copy of this small adapter rather
than importing one from another.

`ToolLoopService` is constructed in `CogBase.__init__`, but corridor itself
is only resolved later, in `cog_load()` (it requires an `await`). This
proxy lets the service hold a stable reference from the start -- satisfying
`ToolLLM` structurally, exactly like a `LiteLLMClient` held directly --
while the real lookup happens on each call, by which point `cog_load()`
has always already run.
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
