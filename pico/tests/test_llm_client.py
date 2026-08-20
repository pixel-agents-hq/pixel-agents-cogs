"""Request payload shape via model_dump, response parsing from canned JSON,
and error handling on non-200/invalid JSON/failed validation, using a
hand-rolled fake aiohttp.ClientSession (no aioresponses) -- mirrors
`floorplan/tests/test_catalogue.py`'s RecordingSession/FakeResponse."""

from __future__ import annotations

import json
import unittest
from typing import Any

import pytest

from ..infrastructure.llm_client import (
    ChatMessage,
    LiteLLMClient,
    LLMRequestError,
    ToolFunctionSpec,
    ToolSpecWire,
)


class FakeResponse:
    def __init__(
        self,
        status: int = 200,
        payload: object = None,
        *,
        text: str = "",
        json_error: Exception | None = None,
    ) -> None:
        self.status = status
        self.payload = payload
        self.text_body = text
        self.json_error = json_error

    async def json(self) -> object:
        if self.json_error is not None:
            raise self.json_error
        return self.payload

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


def _completion_payload(
    content: str | None = "hi there", tool_calls: list[object] | None = None
) -> dict[str, Any]:
    message: dict[str, Any] = {"role": "assistant", "content": content}
    if tool_calls is not None:
        message["tool_calls"] = tool_calls
    return {"choices": [{"message": message, "finish_reason": "stop"}]}


class TestLiteLLMClient(unittest.IsolatedAsyncioTestCase):
    async def test_sends_the_expected_request_shape(self) -> None:
        session = RecordingSession([FakeResponse(payload=_completion_payload())])
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
        self.assertNotIn("name", body["messages"][0])  # exclude_none dropped it

    async def test_strips_a_trailing_slash_from_the_base_url(self) -> None:
        session = RecordingSession([FakeResponse(payload=_completion_payload())])
        client = LiteLLMClient(session_factory=lambda **kw: session)

        await client.complete(
            base_url="https://litellm.example", api_key="k", model="m", messages=[]
        )

        url, _kwargs = session.calls[0]
        self.assertEqual(url, "https://litellm.example/chat/completions")

    async def test_parses_a_successful_response(self) -> None:
        session = RecordingSession(
            [FakeResponse(payload=_completion_payload(content="hello there"))]
        )
        client = LiteLLMClient(session_factory=lambda **kw: session)

        response = await client.complete(base_url="https://x", api_key="k", model="m", messages=[])

        self.assertEqual(response.choices[0].message.content, "hello there")

    async def test_non_200_raises_llm_request_error(self) -> None:
        session = RecordingSession([FakeResponse(status=500, text="internal error")])
        client = LiteLLMClient(session_factory=lambda **kw: session)

        with pytest.raises(LLMRequestError):
            await client.complete(base_url="https://x", api_key="k", model="m", messages=[])

    async def test_invalid_json_raises_llm_request_error(self) -> None:
        session = RecordingSession(
            [FakeResponse(json_error=json.JSONDecodeError("bad json", "", 0))]
        )
        client = LiteLLMClient(session_factory=lambda **kw: session)

        with pytest.raises(LLMRequestError):
            await client.complete(base_url="https://x", api_key="k", model="m", messages=[])

    async def test_response_failing_validation_raises_llm_request_error(self) -> None:
        session = RecordingSession([FakeResponse(payload={"nope": "not a completion"})])
        client = LiteLLMClient(session_factory=lambda **kw: session)

        with pytest.raises(LLMRequestError):
            await client.complete(base_url="https://x", api_key="k", model="m", messages=[])

    async def test_reuses_one_session_across_calls_and_closes_it(self) -> None:
        session = RecordingSession(
            [
                FakeResponse(payload=_completion_payload()),
                FakeResponse(payload=_completion_payload()),
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
