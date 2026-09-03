"""Request payload shape via model_dump, response parsing from canned SSE
chunks, and error handling on non-200/invalid JSON/failed validation, using
a hand-rolled fake aiohttp.ClientSession (no aioresponses) -- mirrors
`floorplan/tests/test_catalogue.py`'s RecordingSession/FakeResponse."""

from __future__ import annotations

import json
import unittest
from typing import Any

import aiohttp
import pytest

from ..infrastructure.llm_client import (
    CONNECT_TIMEOUT_SECONDS,
    READ_TIMEOUT_SECONDS,
    ChatMessage,
    LiteLLMClient,
    LLMRequestError,
    ToolFunctionSpec,
    ToolSpecWire,
)


class FakeContent:
    """Stands in for aiohttp's StreamReader: async-iterates raw SSE lines."""

    def __init__(self, lines: list[bytes]) -> None:
        self._lines = lines

    def __aiter__(self) -> FakeContent:
        self._iter = iter(self._lines)
        return self

    async def __anext__(self) -> bytes:
        try:
            return next(self._iter)
        except StopIteration:
            raise StopAsyncIteration from None


class FakeResponse:
    def __init__(
        self,
        status: int = 200,
        lines: list[bytes] | None = None,
        *,
        text: str = "",
    ) -> None:
        self.status = status
        self.content = FakeContent(lines or [])
        self.text_body = text

    async def text(self) -> str:
        return self.text_body

    async def __aenter__(self) -> FakeResponse:
        return self

    async def __aexit__(self, *args: object) -> bool:
        return False


class RecordingSession:
    def __init__(self, responses: list[FakeResponse | Exception], **kwargs: object) -> None:
        self.responses = responses
        self.created_with = kwargs
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.closed = False

    def post(self, url: str, **kwargs: object) -> FakeResponse:
        self.calls.append((url, kwargs))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    async def close(self) -> None:
        self.closed = True


def _sse_lines(content: str | None = "hi there", *, finish_reason: str = "stop") -> list[bytes]:
    """Two chunks -- one delta carrying the content, one carrying the
    finish_reason -- then [DONE], matching LiteLLM's real streaming shape."""

    chunks: list[dict[str, Any]] = [
        {"choices": [{"index": 0, "delta": {"role": "assistant", "content": content}}]},
        {"choices": [{"index": 0, "delta": {}, "finish_reason": finish_reason}]},
    ]
    lines = [f"data: {json.dumps(chunk)}\n".encode() for chunk in chunks]
    lines.append(b"data: [DONE]\n")
    return lines


class TestLiteLLMClient(unittest.IsolatedAsyncioTestCase):
    async def test_sends_the_expected_request_shape(self) -> None:
        session = RecordingSession([FakeResponse(lines=_sse_lines())])
        client = LiteLLMClient(session_factory=lambda **kw: session)

        await client.complete(
            base_url="https://litellm.example/",
            api_key="sk-test",
            model="gpt-test",
            messages=[ChatMessage(role="user", content="hi")],
            tools=[
                ToolSpecWire(
                    function=ToolFunctionSpec(
                        name="t", description="d", parameters={"type": "object"}
                    )
                )
            ],
            tool_choice="auto",
        )

        url, kwargs = session.calls[0]
        self.assertEqual(url, "https://litellm.example/chat/completions")
        self.assertEqual(kwargs["headers"], {"Authorization": "Bearer sk-test"})
        body = kwargs["json"]
        assert isinstance(body, dict)
        self.assertEqual(body["model"], "gpt-test")
        self.assertEqual(body["messages"], [{"role": "user", "content": "hi"}])
        self.assertEqual(body["tool_choice"], "auto")
        self.assertEqual(body["tools"][0]["function"]["name"], "t")
        self.assertEqual(body["stream"], True)
        self.assertNotIn("name", body["messages"][0])  # exclude_none dropped it

    async def test_strips_a_trailing_slash_from_the_base_url(self) -> None:
        session = RecordingSession([FakeResponse(lines=_sse_lines())])
        client = LiteLLMClient(session_factory=lambda **kw: session)

        await client.complete(
            base_url="https://litellm.example", api_key="k", model="m", messages=[]
        )

        url, _kwargs = session.calls[0]
        self.assertEqual(url, "https://litellm.example/chat/completions")

    async def test_omitted_timeout_does_not_pass_a_per_request_override(self) -> None:
        session = RecordingSession([FakeResponse(lines=_sse_lines())])
        client = LiteLLMClient(session_factory=lambda **kw: session)

        await client.complete(base_url="https://x", api_key="k", model="m", messages=[])

        _url, kwargs = session.calls[0]
        self.assertNotIn("timeout", kwargs)

    async def test_given_timeout_overrides_only_this_request(self) -> None:
        session = RecordingSession([FakeResponse(lines=_sse_lines())])
        client = LiteLLMClient(session_factory=lambda **kw: session)

        await client.complete(
            base_url="https://x", api_key="k", model="m", messages=[], timeout_seconds=90.0
        )

        _url, kwargs = session.calls[0]
        request_timeout = kwargs["timeout"]
        assert isinstance(request_timeout, aiohttp.ClientTimeout)
        self.assertEqual(request_timeout.total, 90.0)
        self.assertEqual(request_timeout.connect, CONNECT_TIMEOUT_SECONDS)
        self.assertEqual(request_timeout.sock_read, READ_TIMEOUT_SECONDS)

    async def test_parses_a_successful_response(self) -> None:
        session = RecordingSession([FakeResponse(lines=_sse_lines(content="hello there"))])
        client = LiteLLMClient(session_factory=lambda **kw: session)

        response = await client.complete(base_url="https://x", api_key="k", model="m", messages=[])

        self.assertEqual(response.choices[0].message.content, "hello there")
        self.assertEqual(response.choices[0].finish_reason, "stop")

    async def test_assembles_tool_calls_streamed_across_chunks(self) -> None:
        chunks = [
            {
                "choices": [
                    {
                        "index": 0,
                        "delta": {
                            "role": "assistant",
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "call_1",
                                    "type": "function",
                                    "function": {"name": "re"},
                                }
                            ],
                        },
                    }
                ]
            },
            {
                "choices": [
                    {
                        "index": 0,
                        "delta": {
                            "tool_calls": [
                                {"index": 0, "function": {"name": "ply", "arguments": '{"a":'}}
                            ]
                        },
                    }
                ]
            },
            {
                "choices": [
                    {
                        "index": 0,
                        "delta": {"tool_calls": [{"index": 0, "function": {"arguments": "1}"}}]},
                    }
                ]
            },
            {"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]},
        ]
        lines = [f"data: {json.dumps(chunk)}\n".encode() for chunk in chunks]
        lines.append(b"data: [DONE]\n")
        session = RecordingSession([FakeResponse(lines=lines)])
        client = LiteLLMClient(session_factory=lambda **kw: session)

        response = await client.complete(base_url="https://x", api_key="k", model="m", messages=[])

        tool_call = response.choices[0].message.tool_calls[0]
        self.assertEqual(tool_call.id, "call_1")
        self.assertEqual(tool_call.function.name, "reply")
        self.assertEqual(tool_call.function.arguments, '{"a":1}')

    async def test_non_200_raises_llm_request_error(self) -> None:
        session = RecordingSession([FakeResponse(status=500, text="internal error")])
        client = LiteLLMClient(session_factory=lambda **kw: session)

        with pytest.raises(LLMRequestError):
            await client.complete(base_url="https://x", api_key="k", model="m", messages=[])

    async def test_invalid_json_raises_llm_request_error(self) -> None:
        session = RecordingSession([FakeResponse(lines=[b"data: not-json\n"])])
        client = LiteLLMClient(session_factory=lambda **kw: session)

        with pytest.raises(LLMRequestError):
            await client.complete(base_url="https://x", api_key="k", model="m", messages=[])

    async def test_reuses_one_session_across_calls_and_closes_it(self) -> None:
        session = RecordingSession(
            [
                FakeResponse(lines=_sse_lines()),
                FakeResponse(lines=_sse_lines()),
            ]
        )
        creations: list[dict[str, object]] = []

        def factory(**kwargs: object) -> RecordingSession:
            creations.append(kwargs)
            return session

        client = LiteLLMClient(session_factory=factory)
        await client.start()
        await client.complete(base_url="https://x", api_key="k", model="m", messages=[])
        await client.complete(base_url="https://x", api_key="k", model="m", messages=[])

        self.assertEqual(len(creations), 1)

        await client.close()

        self.assertTrue(session.closed)
