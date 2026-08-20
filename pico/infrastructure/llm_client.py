"""Pydantic wire models for LiteLLM's OpenAI-compatible proxy, plus a thin
aiohttp client. Raw `aiohttp` POST to `/chat/completions` -- no `openai`
SDK, no `litellm` pip package -- matches floorplan's existing aiohttp
precedent (`floorplan/infrastructure/pixel_index.py`) instead of adding a
new client dependency."""

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
        self._log = logger or logging.getLogger("red.pico")
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
    ) -> ChatCompletionResponse:
        request = ChatCompletionRequest(
            model=model,
            messages=list(messages),
            tools=list(tools) if tools else None,
            tool_choice=tool_choice,
        )
        body = request.model_dump(mode="json", exclude_none=True)
        url = f"{base_url.rstrip('/')}/chat/completions"
        try:
            session = await self._get_session()
            async with session.post(
                url, json=body, headers={"Authorization": f"Bearer {api_key}"}
            ) as response:
                if response.status != 200:
                    text = await response.text()
                    raise LLMRequestError(f"LiteLLM returned HTTP {response.status}: {text[:200]}")
                try:
                    payload = await response.json()
                except (json.JSONDecodeError, aiohttp.ContentTypeError, UnicodeDecodeError) as exc:
                    raise LLMRequestError(f"LiteLLM returned invalid JSON: {exc}") from exc
        except TimeoutError as exc:
            raise LLMRequestError(f"LiteLLM request timed out: {exc}") from exc
        except (aiohttp.ClientError, OSError) as exc:
            raise LLMRequestError(f"Could not reach LiteLLM: {exc}") from exc

        try:
            return ChatCompletionResponse.model_validate(payload)
        except ValidationError as exc:
            raise LLMRequestError(f"LiteLLM response failed validation: {exc}") from exc

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
