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

from animator.application import ToolLoopResult as AnimatorToolLoopResult
from animator.domain import GlobalSettings as AnimatorGlobalSettings
from animator.infrastructure.a2a_server import AnimatorAgentExecutor
from animator.infrastructure.a2a_server import build_agent_card as build_animator_agent_card
from animator.tools.attachment import Attachment as AnimatorAttachment
from architect.application import ToolLoopResult
from architect.domain import GlobalSettings
from architect.infrastructure.a2a_server import ArchitectAgentExecutor, build_agent_card
from corridor.domain import RegisteredAgent, card_with_url
from corridor.infrastructure.a2a_server import A2AServer

from ..infrastructure import architect_client as architect_client_module
from ..infrastructure.architect_client import (
    AgentAskResult,
    ArchitectClient,
    ArchitectRequestError,
    Attachment,
)

_PORT = 8935
_BASE_URL = f"http://127.0.0.1:{_PORT}/architect/"
_ANIMATOR_PORT = 8936
_ANIMATOR_BASE_URL = f"http://127.0.0.1:{_ANIMATOR_PORT}/animator/"


class _FakeArchitectLLMSettings:
    llm_base_url = "https://example.test/"
    llm_api_key: str | None = "sk-test"
    llm_model: str | None = "test-model"

    @property
    def ready(self) -> bool:
        return True


class _ScriptedToolLoop:
    def __init__(self, result: ToolLoopResult, *, debug_events: list[str] | None = None) -> None:
        self._result = result
        self._debug_events = debug_events or []

    async def run(self, **kwargs: object) -> ToolLoopResult:
        on_debug_event = kwargs.get("on_debug_event")
        if on_debug_event is not None:
            for event in self._debug_events:
                await on_debug_event(event)  # type: ignore[operator]
        return self._result


async def _settings() -> GlobalSettings:
    return GlobalSettings(
        max_tool_calls=5,
        system_prompt="sys",
        debug_logging=False,
    )


async def _llm_settings() -> _FakeArchitectLLMSettings:
    return _FakeArchitectLLMSettings()


class TestArchitectClientLiveRoundTrip(unittest.IsolatedAsyncioTestCase):
    async def _start_server(
        self, result: ToolLoopResult, *, debug_events: list[str] | None = None
    ) -> A2AServer:
        executor = ArchitectAgentExecutor(
            tool_loop=_ScriptedToolLoop(result, debug_events=debug_events),
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

    async def test_ask_streams_intermediate_debug_events_via_on_activity(self) -> None:
        """Real round trip covering the new streaming path: architect emits
        TASK_STATE_WORKING status updates mid-task (see
        architect/infrastructure/a2a_server.py's _emit_debug), and this
        confirms pico's client (a) actually receives them over the real
        wire now that both sides advertise/request streaming, (b) invokes
        on_activity with each one in order, and (c) still resolves the true
        final answer correctly, not one of the intermediate events."""

        await self._start_server(
            ToolLoopResult(
                1, "final_text", "moved the table", successful_tool_calls=1, failed_tool_calls=0
            ),
            debug_events=["thinking: let me check", "calling move_furniture({...})"],
        )
        seen: list[str] = []

        async def on_activity(text: str) -> None:
            seen.append(text)

        result = await ArchitectClient().ask(base_url=_BASE_URL, text="hi", on_activity=on_activity)

        self.assertEqual(seen, ["thinking: let me check", "calling move_furniture({...})"])
        self.assertEqual(result.answer, "moved the table")

    async def test_ask_without_on_activity_ignores_intermediate_debug_events(self) -> None:
        await self._start_server(
            ToolLoopResult(
                1, "final_text", "moved the table", successful_tool_calls=1, failed_tool_calls=0
            ),
            debug_events=["thinking: let me check"],
        )

        result = await ArchitectClient().ask(base_url=_BASE_URL, text="hi")

        self.assertEqual(result.answer, "moved the table")

    async def test_ask_raises_when_architect_task_fails(self) -> None:
        """Regression test: `GenericAgentExecutor._run_turn`'s
        `updater.failed(...)` calls never attach a `metadata=` dict at all
        -- a real production incident had this surface here as an opaque
        `ArchitectRequestError("architect request failed: Value not
        set")` (see `_metadata_int`'s own docstring for why a *missing*
        Struct key raises `ValueError`, not `KeyError`) instead of
        architect's own real, human-readable failure message."""

        await self._start_server(
            ToolLoopResult(5, "max_tool_calls", None, successful_tool_calls=3, failed_tool_calls=2)
        )

        with self.assertRaises(ArchitectRequestError) as raised:
            await ArchitectClient().ask(base_url=_BASE_URL, text="hi")

        self.assertIn("could not produce an answer", str(raised.exception))
        self.assertNotIn("Value not set", str(raised.exception))

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


class _FakeAnimatorLLMSettings:
    llm_base_url = "https://example.test/"
    llm_api_key: str | None = "sk-test"
    llm_model: str | None = "test-model"

    @property
    def ready(self) -> bool:
        return True


class _ScriptedAnimatorToolLoop:
    def __init__(self, result: AnimatorToolLoopResult) -> None:
        self._result = result

    async def run(self, **kwargs: object) -> AnimatorToolLoopResult:
        return self._result


async def _animator_settings() -> AnimatorGlobalSettings:
    return AnimatorGlobalSettings(max_tool_calls=5, system_prompt="sys", debug_logging=False)


async def _animator_llm_settings() -> _FakeAnimatorLLMSettings:
    return _FakeAnimatorLLMSettings()


class TestArchitectClientCollectsAttachments(unittest.IsolatedAsyncioTestCase):
    """Real round trip against animator's own executor (the only agent
    that ever sends `raw` `Part`s today, see `corridor.domain.
    agent_executor.GenericAgentExecutor._run_turn`'s `attachments`
    handling) -- confirms pico's client reconstructs them correctly
    through the real A2A wire format, not a mock."""

    async def _start_animator_server(self, result: AnimatorToolLoopResult) -> A2AServer:
        executor = AnimatorAgentExecutor(
            tool_loop=_ScriptedAnimatorToolLoop(result),
            tools=[],
            settings=_animator_settings,
            llm_settings=_animator_llm_settings,
        )
        card = card_with_url(build_animator_agent_card(tools=[]), _ANIMATOR_BASE_URL)
        agent = RegisteredAgent(agent_key="animator", card=card, executor=executor)
        server = A2AServer()
        await server.start(host="127.0.0.1", port=_ANIMATOR_PORT, agents=[agent])
        self.addAsyncCleanup(server.stop)
        return server

    async def test_ask_returns_no_attachments_when_none_were_sent(self) -> None:
        await self._start_animator_server(
            AnimatorToolLoopResult(
                0, "final_text", "no files here", successful_tool_calls=0, failed_tool_calls=0
            )
        )

        result = await ArchitectClient().ask(base_url=_ANIMATOR_BASE_URL, text="hi")

        self.assertEqual(result.attachments, ())

    async def test_ask_reconstructs_attachments_sent_by_the_consulted_agent(self) -> None:
        await self._start_animator_server(
            AnimatorToolLoopResult(
                1,
                "final_text",
                "attached the files",
                successful_tool_calls=1,
                failed_tool_calls=0,
                attachments=(
                    AnimatorAttachment(
                        filename="pixel-agents.zip", media_type="application/zip", data=b"ZIPBYTES"
                    ),
                    AnimatorAttachment(
                        filename="preview.png", media_type="image/png", data=b"PNGBYTES"
                    ),
                ),
            )
        )

        result = await ArchitectClient().ask(base_url=_ANIMATOR_BASE_URL, text="render it")

        self.assertEqual(result.answer, "attached the files")
        self.assertEqual(
            result.attachments,
            (
                Attachment(
                    filename="pixel-agents.zip", media_type="application/zip", data=b"ZIPBYTES"
                ),
                Attachment(filename="preview.png", media_type="image/png", data=b"PNGBYTES"),
            ),
        )


class TestMetadataInt(unittest.TestCase):
    """Direct unit coverage for `_metadata_int` against a real
    `google.protobuf.struct_pb2.Struct` -- see its own docstring for why a
    wholly *absent* key raises `ValueError`, not `KeyError`, unlike a plain
    dict."""

    def test_present_numeric_key_returns_it(self) -> None:
        from google.protobuf import struct_pb2

        metadata = struct_pb2.Struct()
        metadata.update({"tool_calls_made": 4})

        self.assertEqual(architect_client_module._metadata_int(metadata, "tool_calls_made"), 4)

    def test_wholly_absent_key_returns_none_not_raise(self) -> None:
        from google.protobuf import struct_pb2

        metadata = struct_pb2.Struct()

        self.assertIsNone(architect_client_module._metadata_int(metadata, "tool_calls_made"))

    def test_present_non_numeric_key_returns_none(self) -> None:
        from google.protobuf import struct_pb2

        metadata = struct_pb2.Struct()
        metadata.update({"tool_calls_made": "not a number"})

        self.assertIsNone(architect_client_module._metadata_int(metadata, "tool_calls_made"))
