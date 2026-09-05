"""Shared test doubles for the multi-cog e2e harness.

The only thing mocked anywhere in this suite is the LLM API boundary
(`ScriptedLLM`, standing in for the real LiteLLM proxy) and the Discord
gateway (`FakeBot`, standing in for a live `redbot.core.bot.Red`) -- every
tool architect/painter call, the office layout codec, corridor's Config
and pub/sub, and cctv's serving stack are the real production classes.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from corridor.infrastructure.llm_client import (
    ChatCompletionChoice,
    ChatCompletionResponse,
    ChatCompletionResponseMessage,
    ToolCall,
    ToolCallFunction,
)


@dataclass
class FakeUser:
    id: int = 1
    name: str = "e2e-harness"
    bot: bool = True


class FakeBot:
    """Minimal `Red` double shared by every real cog constructed in this
    harness.

    `get_cog`/`add_cog` back every cross-cog `ensure_loaded`/
    `ensure_corridor_loaded` call (`corridor/dependency_loader.py`), which
    each short-circuit the instant `get_cog(name)` returns non-`None` --
    so as long as corridor and pixelagents are `add_cog`-ed onto this same
    bot before the cogs that depend on them are constructed and loaded, no
    cog-manager/extension-loading machinery is ever reached, and every
    dependent resolves the real, already-`cog_load()`-ed instance.
    """

    def __init__(self, owner_ids: frozenset[int] = frozenset({1})) -> None:
        self.owner_ids = owner_ids
        self.user = FakeUser()
        self.guilds: list[Any] = []
        self._cogs: dict[str, Any] = {}
        self.owner_notifications: list[str] = []

    def get_guild(self, guild_id: int) -> Any:
        return None

    def add_cog(self, cog: Any) -> None:
        self._cogs[type(cog).__name__] = cog

    def get_cog(self, name: str) -> Any:
        return self._cogs.get(name)

    @property
    def cogs(self) -> dict[str, Any]:
        return dict(self._cogs)

    async def get_valid_prefixes(self) -> list[str]:
        return [";"]

    async def is_owner(self, user: Any) -> bool:
        return getattr(user, "id", None) in self.owner_ids

    async def send_to_owners(self, message: str) -> None:
        self.owner_notifications.append(message)

    async def wait_until_red_ready(self) -> None:
        return

    async def unload_extension(self, name: str) -> None:
        return


class ScriptedLLM:
    """`ToolLoopService`'s LLM client double: `.complete(**kwargs)` returns
    the next scripted response (or raises it, if it's an exception).
    Mirrors architect/tests/test_tool_loop_service.py's own fake -- the
    same "mock only the wire response, run every tool for real" boundary,
    just reused outside that one test module."""

    def __init__(self, responses: list[ChatCompletionResponse | Exception]) -> None:
        self._responses = list(responses)

    async def complete(self, **kwargs: object) -> ChatCompletionResponse:
        del kwargs
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def tool_call_response(
    name: str, arguments: dict[str, object], *, call_id: str = "1"
) -> ChatCompletionResponse:
    """One `ToolLoopService.run()` turn that calls tool `name` with
    `arguments` and nothing else."""

    return ChatCompletionResponse(
        choices=[
            ChatCompletionChoice(
                message=ChatCompletionResponseMessage(
                    role="assistant",
                    content=None,
                    tool_calls=[
                        ToolCall(
                            id=call_id,
                            function=ToolCallFunction(name=name, arguments=json.dumps(arguments)),
                        )
                    ],
                )
            )
        ]
    )


def final_response(text: str = "done") -> ChatCompletionResponse:
    """The turn that ends a `ToolLoopService.run()` loop: plain content, no
    further tool calls."""

    return ChatCompletionResponse(
        choices=[
            ChatCompletionChoice(
                message=ChatCompletionResponseMessage(
                    role="assistant", content=text, tool_calls=None
                )
            )
        ]
    )
