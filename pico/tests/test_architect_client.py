"""ArchitectClient against a real, live loopback A2A listener -- this is
the actual pico<->architect round trip docs/architect-design.md's sequence
diagram describes, not a fake, now mounted on corridor's shared A2A
listener rather than architect's own (see docs/agent-directory-design.md).
Only this one test file imports `architect` directly (a test-only
dependency, never a runtime one -- pico's own production code never
imports architect, see docs/architect-design.md section 7 on why that
edge is networked, not `required_cogs`)."""

from __future__ import annotations

import unittest
from unittest.mock import patch

import httpx

from architect.application import ToolLoopResult
from architect.domain import GlobalSettings
from architect.infrastructure.a2a_server import ArchitectAgentExecutor, build_agent_card
from corridor.domain import RegisteredAgent, card_with_url
from corridor.infrastructure.a2a_server import A2AServer

from ..infrastructure import architect_client as architect_client_module
from ..infrastructure.architect_client import AgentAskResult, ArchitectClient, ArchitectRequestError

_PORT = 8935
_BASE_URL = f"http://127.0.0.1:{_PORT}/architect/"


class _FakeArchitectLLMSettings:
    llm_base_url = "https://example.test/"
    llm_api_key: str | None = "sk-test"
    llm_model: str | None = "test-model"

    @property
    def ready(self) -> bool:
        return True


class _ScriptedToolLoop:
    def __init__(self, result: ToolLoopResult) -> None:
        self._result = result

    async def run(self, **kwargs: object) -> ToolLoopResult:
        return self._result


async def _settings() -> GlobalSettings:
    return GlobalSettings(
        max_tool_calls=5,
        system_prompt="sys",
        ws_host="127.0.0.1",
        ws_port=_PORT + 1000,
        debug_logging=False,
    )


async def _llm_settings() -> _FakeArchitectLLMSettings:
    return _FakeArchitectLLMSettings()


class TestArchitectClientLiveRoundTrip(unittest.IsolatedAsyncioTestCase):
    async def _start_server(self, result: ToolLoopResult) -> A2AServer:
        executor = ArchitectAgentExecutor(
            tool_loop=_ScriptedToolLoop(result),
            tools=[],
            settings=_settings,
            llm_settings=_llm_settings,
        )
        # Mirrors what corridor.register_agent does for real: overwrite the
        # placeholder card URL with the URL this listener will actually be
        # reachable at (see docs/agent-directory-design.md) -- a2a-sdk's
        # client discovers the agent card first and then sends its real
        # JSON-RPC calls to *that* URL, not the one `ask()` was given.
        card = card_with_url(build_agent_card(tools=[]), _BASE_URL)
        agent = RegisteredAgent(agent_key="architect", card=card, executor=executor)
        server = A2AServer()
        await server.start(host="127.0.0.1", port=_PORT, agents=[agent])
        self.addAsyncCleanup(server.stop)
        return server

    async def test_ask_returns_architects_final_text(self) -> None:
        await self._start_server(
            ToolLoopResult(
                0,
                "final_text",
                "hello from architect",
                successful_tool_calls=0,
                failed_tool_calls=0,
            )
        )

        result = await ArchitectClient().ask(base_url=_BASE_URL, text="hi")

        self.assertEqual(
            result,
            AgentAskResult(
                answer="hello from architect",
                tool_calls_made=0,
                successful_tool_calls=0,
                failed_tool_calls=0,
            ),
        )

    async def test_ask_returns_the_tool_calls_architect_actually_made(self) -> None:
        """Real round trip: architect's ArchitectAgentExecutor attaches
        `tool_calls_made`/`successful_tool_calls`/`failed_tool_calls` as
        metadata on its final message
        (architect/infrastructure/a2a_server.py), and this asserts pico's
        client reads them back correctly through the real wire format, not
        a mock -- the fields the "📩 ... replied" Discord embed surfaces."""

        await self._start_server(
            ToolLoopResult(
                3, "final_text", "moved the table", successful_tool_calls=2, failed_tool_calls=1
            )
        )

        result = await ArchitectClient().ask(base_url=_BASE_URL, text="hi")

        self.assertEqual(
            result,
            AgentAskResult(
                answer="moved the table",
                tool_calls_made=3,
                successful_tool_calls=2,
                failed_tool_calls=1,
            ),
        )

    async def test_ask_raises_when_architect_task_fails(self) -> None:
        await self._start_server(
            ToolLoopResult(5, "max_tool_calls", None, successful_tool_calls=3, failed_tool_calls=2)
        )

        with self.assertRaises(ArchitectRequestError):
            await ArchitectClient().ask(base_url=_BASE_URL, text="hi")

    async def test_ask_raises_when_architect_is_unreachable(self) -> None:
        with self.assertRaises(ArchitectRequestError):
            await ArchitectClient().ask(base_url="http://127.0.0.1:1/", text="hi")

    async def test_ask_uses_a_generous_client_timeout_not_httpxs_5s_default(self) -> None:
        """Regression test for a real production incident: httpx's own
        default timeout (5.0s, every phase) is far too short for a call
        that waits on architect's entire bounded tool-calling loop, which
        can make several sequential corridor LLM round trips before
        returning -- a live deployment saw `consult_architect failed:
        Client Request timed out` exactly 5s after the request went out.
        """
        await self._start_server(
            ToolLoopResult(
                0,
                "final_text",
                "hello from architect",
                successful_tool_calls=0,
                failed_tool_calls=0,
            )
        )

        real_client = httpx.AsyncClient(timeout=architect_client_module._REQUEST_TIMEOUT_SECONDS)
        self.addAsyncCleanup(real_client.aclose)
        with patch.object(
            architect_client_module.httpx, "AsyncClient", return_value=real_client
        ) as mock_client:
            await ArchitectClient().ask(base_url=_BASE_URL, text="hi")

        mock_client.assert_called_once_with(
            timeout=architect_client_module._REQUEST_TIMEOUT_SECONDS
        )
        self.assertGreater(
            architect_client_module._REQUEST_TIMEOUT_SECONDS,
            httpx._config.DEFAULT_TIMEOUT_CONFIG.read,
        )
