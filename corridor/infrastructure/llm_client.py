"""Pydantic wire models for LiteLLM's OpenAI-compatible proxy, plus a thin
aiohttp client. Raw `aiohttp` POST to `/chat/completions` -- no `openai`
SDK, no `litellm` pip package -- matches floorplan's existing aiohttp
precedent (`floorplan/infrastructure/pixel_index.py`) instead of adding a
new client dependency.

Lives in corridor, not pico, because both pico and architect share one LLM
connection -- see docs/architect-design.md's LLM provider migration
section. Originally written for pico alone; the shape is unchanged by the
move.

Always requests `stream=True` and reassembles the SSE chunks into a single
response dict before validating it through the same wire models a
non-streaming call would produce. This works around a LiteLLM bug in its
`chatgpt/*` (ChatGPT-subscription/Codex) provider: its non-streaming path
returns an empty `output` array even when the model generated text, so
`stream=False` requests fail every time. Streaming is unaffected."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Sequence
from typing import Any, Literal

import aiohttp
from pydantic import BaseModel, ConfigDict, ValidationError

REQUEST_TIMEOUT_SECONDS = 30.0
CONNECT_TIMEOUT_SECONDS = 5.0
READ_TIMEOUT_SECONDS = 25.0

SessionFactory = Callable[..., aiohttp.ClientSession]


class LLMRequestError(RuntimeError):
    """Raised on any failure to obtain a valid chat completion -- non-200
    status, invalid JSON, or a response that fails wire-model validation.
    Callers (GateService/ToolLoopService) catch this and fail closed."""


class ToolCallFunction(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str
    arguments: str


class ToolCall(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    type: Literal["function"] = "function"
    function: ToolCallFunction


class ChatMessage(BaseModel):
    model_config = ConfigDict(extra="ignore")

    role: Literal["system", "user", "assistant", "tool"]
    content: str | None = None
    name: str | None = None
    tool_call_id: str | None = None
    tool_calls: list[ToolCall] | None = None


class ToolFunctionSpec(BaseModel):
    name: str
    description: str
    parameters: dict[str, Any]


class ToolSpecWire(BaseModel):
    """The OpenAI-compatible `tools=[...]` entry shape."""

    type: Literal["function"] = "function"
    function: ToolFunctionSpec


class ChatCompletionRequest(BaseModel):
    model: str
    messages: list[ChatMessage]
    tools: list[ToolSpecWire] | None = None
    tool_choice: str | None = None
    stream: bool = True


class ChatCompletionResponseMessage(BaseModel):
    model_config = ConfigDict(extra="ignore")

    role: str
    content: str | None = None
    tool_calls: list[ToolCall] | None = None


class ChatCompletionChoice(BaseModel):
    model_config = ConfigDict(extra="ignore")

    message: ChatCompletionResponseMessage
    finish_reason: str | None = None


class ChatCompletionResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    choices: list[ChatCompletionChoice]


class LiteLLMClient:
    """One reusable aiohttp session per Cog lifetime, POSTing to LiteLLM's
    `/chat/completions` endpoint. Mirrors PixelIndexClient's lifecycle
    shape (`floorplan/infrastructure/pixel_index.py`): lazy/explicit
    start(), idempotent close(), one shared session."""

    def __init__(
        self,
        *,
        session_factory: SessionFactory | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._log = logger or logging.getLogger("red.corridor")
        self._session: aiohttp.ClientSession | None = None
        self._timeout = aiohttp.ClientTimeout(
            total=REQUEST_TIMEOUT_SECONDS,
            connect=CONNECT_TIMEOUT_SECONDS,
            sock_read=READ_TIMEOUT_SECONDS,
        )

    @property
    def running(self) -> bool:
        return self._session is not None and not self._session.closed

    async def start(self) -> None:
        """Open the shared session once; safe to call repeatedly."""

        if self.running:
            return
        factory = self._session_factory or aiohttp.ClientSession
        self._session = factory(timeout=self._timeout)

    async def close(self) -> None:
        """Close the shared session once; safe before or after startup."""

        session = self._session
        self._session = None
        if session is not None and not session.closed:
            await session.close()

    async def complete(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        messages: Sequence[ChatMessage],
        tools: Sequence[ToolSpecWire] | None = None,
        tool_choice: str | None = None,
        timeout_seconds: float | None = None,
    ) -> ChatCompletionResponse:
        """`timeout_seconds`, when given, overrides this one request's
        total timeout (connect/sock_read stay at the session's own
        constants) -- the session-wide `REQUEST_TIMEOUT_SECONDS` default
        otherwise applies unchanged. aiohttp's per-request `timeout=`
        kwarg fully replaces the session default rather than merging with
        it, so a complete `ClientTimeout` is rebuilt here rather than only
        overriding `total`. (Named `timeout_seconds`, not `timeout`, on
        this async method -- ruff's ASYNC109 flags a bare `timeout`
        parameter as if it were manually reimplementing
        `asyncio.timeout()`, which this isn't: the value is only ever
        forwarded to aiohttp's own per-request timeout config below.)"""

        request = ChatCompletionRequest(
            model=model,
            messages=list(messages),
            tools=list(tools) if tools else None,
            tool_choice=tool_choice,
        )
        body = request.model_dump(mode="json", exclude_none=True)
        url = f"{base_url.rstrip('/')}/chat/completions"
        # aiohttp distinguishes "timeout not passed at all" (falls back to
        # the session's own ClientTimeout) from `timeout=None` (disables
        # timeout entirely) via a sentinel default -- so the kwarg is only
        # added at all when a per-request override is actually requested,
        # never passed through as an explicit None.
        post_kwargs: dict[str, Any] = {
            "json": body,
            "headers": {"Authorization": f"Bearer {api_key}"},
        }
        if timeout_seconds is not None:
            post_kwargs["timeout"] = aiohttp.ClientTimeout(
                total=timeout_seconds,
                connect=CONNECT_TIMEOUT_SECONDS,
                sock_read=READ_TIMEOUT_SECONDS,
            )
        try:
            session = await self._get_session()
            async with session.post(url, **post_kwargs) as response:
                if response.status != 200:
                    text = await response.text()
                    raise LLMRequestError(f"LiteLLM returned HTTP {response.status}: {text[:200]}")
                try:
                    payload = await self._collect_stream(response)
                except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                    raise LLMRequestError(f"LiteLLM returned invalid JSON: {exc}") from exc
        except TimeoutError as exc:
            raise LLMRequestError(f"LiteLLM request timed out: {exc}") from exc
        except (aiohttp.ClientError, OSError) as exc:
            raise LLMRequestError(f"Could not reach LiteLLM: {exc}") from exc

        try:
            return ChatCompletionResponse.model_validate(payload)
        except ValidationError as exc:
            raise LLMRequestError(f"LiteLLM response failed validation: {exc}") from exc

    @staticmethod
    async def _collect_stream(response: aiohttp.ClientResponse) -> dict[str, Any]:
        """Reassemble an SSE `chat/completions` stream into a single
        OpenAI-shaped response dict (one entry per choice index)."""

        choices: dict[int, dict[str, Any]] = {}
        async for raw_line in response.content:
            line = raw_line.decode("utf-8").strip()
            if not line.startswith("data:"):
                continue
            data = line[len("data:") :].strip()
            if data == "[DONE]":
                break
            chunk = json.loads(data)
            for choice in chunk.get("choices", []):
                index = choice.get("index", 0)
                entry = choices.setdefault(
                    index,
                    {"message": {"role": "assistant", "content": None}, "finish_reason": None},
                )
                delta = choice.get("delta", {})
                if delta.get("role"):
                    entry["message"]["role"] = delta["role"]
                if delta.get("content"):
                    entry["message"]["content"] = (entry["message"]["content"] or "") + delta[
                        "content"
                    ]
                if delta.get("tool_calls"):
                    tool_calls = entry["message"].setdefault("tool_calls", [])
                    for tc_delta in delta["tool_calls"]:
                        tc_index = tc_delta.get("index", 0)
                        while len(tool_calls) <= tc_index:
                            tool_calls.append(
                                {
                                    "id": "",
                                    "type": "function",
                                    "function": {"name": "", "arguments": ""},
                                }
                            )
                        tool_call = tool_calls[tc_index]
                        if tc_delta.get("id"):
                            tool_call["id"] = tc_delta["id"]
                        if tc_delta.get("type"):
                            tool_call["type"] = tc_delta["type"]
                        fn_delta = tc_delta.get("function") or {}
                        if fn_delta.get("name"):
                            tool_call["function"]["name"] += fn_delta["name"]
                        if fn_delta.get("arguments"):
                            tool_call["function"]["arguments"] += fn_delta["arguments"]
                if choice.get("finish_reason"):
                    entry["finish_reason"] = choice["finish_reason"]
        return {"choices": [choices[index] for index in sorted(choices)]}

    async def _get_session(self) -> aiohttp.ClientSession:
        if not self.running:
            await self.start()
        assert self._session is not None
        return self._session


__all__ = [
    "ChatCompletionChoice",
    "ChatCompletionRequest",
    "ChatCompletionResponse",
    "ChatCompletionResponseMessage",
    "ChatMessage",
    "LLMRequestError",
    "LiteLLMClient",
    "ToolCall",
    "ToolCallFunction",
    "ToolFunctionSpec",
    "ToolSpecWire",
]
