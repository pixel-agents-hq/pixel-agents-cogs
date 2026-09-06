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
`stream=False` requests fail every time. Streaming is unaffected.

`list_models()` additionally fronts LiteLLM's OpenAI-compatible
`GET /v1/models` (+ best-effort `GET /model/info` enrichment to drop
embedding models by their declared mode) for the
`[p]corridor llm model` picker -- see `ModelCatalogService`
(`corridor/application/model_catalog_service.py`) for the caching/
degradation layer built on top of it, ported down from tinytinkerer's
edge (apps/edge/src/routes/models.ts, apps/edge/src/lib/models-cache.ts)."""

from __future__ import annotations

import json
import logging
import re
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


class ModelListEntry(BaseModel):
    """One entry of LiteLLM's OpenAI-compatible `GET /v1/models` catalogue."""

    model_config = ConfigDict(extra="ignore")

    id: str
    object: str | None = None
    owned_by: str | None = None


class ModelListResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    data: list[ModelListEntry] = []


class ModelInfoDetail(BaseModel):
    model_config = ConfigDict(extra="ignore")

    mode: str | None = None


class ModelInfoEntry(BaseModel):
    """One entry of LiteLLM's `GET /model/info` -- richer than
    `/v1/models` (carries `mode`), but not every deployment/virtual key
    serves it, so callers must treat a failure here as best-effort."""

    model_config = ConfigDict(extra="ignore")

    model_name: str
    model_info: ModelInfoDetail | None = None


class ModelInfoResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    data: list[ModelInfoEntry] = []


# `/v1/models` carries no `mode`, so when `/model/info` is unavailable the
# model NAME is the only embedding signal: match 'embedding' anywhere plus
# 'embed' as a standalone token (e.g. cohere/embed-english-v3.0). Mirrors
# tinytinkerer's apps/edge/src/routes/models.ts looksLikeEmbeddingModel.
_EMBED_TOKEN_RE = re.compile(r"(^|[^a-z])embed($|[^a-z])")


def _looks_like_embedding_model(model_id: str) -> bool:
    lowered = model_id.lower()
    return "embedding" in lowered or bool(_EMBED_TOKEN_RE.search(lowered))


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

    async def list_models(self, *, base_url: str, api_key: str) -> list[str]:
        """The sorted, deduped, non-embedding model ids this virtual key
        can use, for the `[p]corridor llm model` picker. GET
        `{base_url}/v1/models` is required -- a failure there raises
        `LLMRequestError`, same failure contract as `complete()`.
        `{base_url}/model/info` enrichment (dropping embedding models by
        their declared `mode` instead of the name heuristic) is best-
        effort: older LiteLLM deployments or restricted keys may not serve
        it, so any failure there is swallowed by `_model_modes` rather
        than surfaced here. Mirrors tinytinkerer's
        apps/edge/src/routes/models.ts model-list route."""

        url = f"{base_url.rstrip('/')}/v1/models"
        headers = {"Authorization": f"Bearer {api_key}"}
        try:
            session = await self._get_session()
            async with session.get(url, headers=headers) as response:
                if response.status != 200:
                    text = await response.text()
                    raise LLMRequestError(f"LiteLLM returned HTTP {response.status}: {text[:200]}")
                try:
                    payload = await response.json(content_type=None)
                except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                    raise LLMRequestError(f"LiteLLM returned invalid JSON: {exc}") from exc
        except TimeoutError as exc:
            raise LLMRequestError(f"LiteLLM request timed out: {exc}") from exc
        except (aiohttp.ClientError, OSError) as exc:
            raise LLMRequestError(f"Could not reach LiteLLM: {exc}") from exc

        try:
            catalogue = ModelListResponse.model_validate(payload)
        except ValidationError as exc:
            raise LLMRequestError(f"LiteLLM model list failed validation: {exc}") from exc

        modes = await self._model_modes(base_url=base_url, api_key=api_key)
        models: set[str] = set()
        for entry in catalogue.data:
            model_id = entry.id.strip()
            if not model_id:
                continue
            mode = modes.get(model_id)
            is_embedding = (
                mode == "embedding" if mode is not None else _looks_like_embedding_model(model_id)
            )
            if not is_embedding:
                models.add(model_id)
        return sorted(models)

    async def _model_modes(self, *, base_url: str, api_key: str) -> dict[str, str]:
        """Best-effort `id -> mode` lookup from `/model/info` -- returns an
        empty map on ANY failure (non-200, unreachable, timeout, malformed
        body), never raises. Only called from `list_models`, which falls
        back to the embedding-name heuristic when this comes back empty."""

        url = f"{base_url.rstrip('/')}/model/info"
        headers = {"Authorization": f"Bearer {api_key}"}
        try:
            session = await self._get_session()
            async with session.get(url, headers=headers) as response:
                if response.status != 200:
                    return {}
                payload = await response.json(content_type=None)
            info = ModelInfoResponse.model_validate(payload)
        except (TimeoutError, aiohttp.ClientError, OSError, json.JSONDecodeError, ValidationError):
            return {}

        modes: dict[str, str] = {}
        for entry in info.data:
            detail = entry.model_info
            if detail is not None and detail.mode:
                modes[entry.model_name.strip()] = detail.mode.strip().lower()
        return modes

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
    "ModelInfoDetail",
    "ModelInfoEntry",
    "ModelInfoResponse",
    "ModelListEntry",
    "ModelListResponse",
    "ToolCall",
    "ToolCallFunction",
    "ToolFunctionSpec",
    "ToolSpecWire",
]
