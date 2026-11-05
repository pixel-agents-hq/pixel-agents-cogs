"""The guild a genuine agent's turn works for: carried as A2A metadata,
set by `GenericAgentExecutor` for the turn, read by the agent's activity
publisher."""

from __future__ import annotations

import logging
import unittest

from a2a.types import Message, Part, Role

from ..domain import (
    A2A_GUILD_METADATA_KEY,
    LLMSettings,
    activity_guild,
    activity_guild_id,
    guild_from_metadata,
    guild_metadata,
)
from ..domain.agent_executor import GenericAgentExecutor
from .test_agent_executor import (
    FakeAgentSettings,
    FakeEventQueue,
    FakeRequestContext,
    FakeToolLoopResult,
)

GUILD_ID = 123456789012345678  # past 2**53: must survive as a string


class TestMetadata(unittest.TestCase):
    def test_round_trips_a_snowflake_through_a_protobuf_message(self) -> None:
        message = Message(
            message_id="m",
            role=Role.ROLE_USER,
            parts=[Part(text="x")],
            metadata=guild_metadata(GUILD_ID),
        )
        self.assertEqual(guild_from_metadata(message.metadata), GUILD_ID)

    def test_unknown_guild_sends_no_metadata(self) -> None:
        self.assertEqual(guild_metadata(None), {})
        self.assertIsNone(guild_from_metadata(Message(message_id="m").metadata))

    def test_malformed_values_read_as_unknown(self) -> None:
        for raw in ("", "abc", "-5", "0", True, 1.5, None, ["1"]):
            with self.subTest(raw=raw):
                self.assertIsNone(guild_from_metadata({A2A_GUILD_METADATA_KEY: raw}))
        self.assertIsNone(guild_from_metadata(None))
        self.assertIsNone(guild_from_metadata(object()))

    def test_activity_guild_scopes_and_restores(self) -> None:
        self.assertIsNone(activity_guild_id())
        with activity_guild(GUILD_ID):
            self.assertEqual(activity_guild_id(), GUILD_ID)
            with activity_guild(None):
                self.assertIsNone(activity_guild_id())
            self.assertEqual(activity_guild_id(), GUILD_ID)
        self.assertIsNone(activity_guild_id())


class _RecordingToolLoop:
    def __init__(self) -> None:
        self.seen: list[int | None] = []

    async def run(self, **kwargs: object) -> FakeToolLoopResult:
        self.seen.append(activity_guild_id())
        return FakeToolLoopResult(0, "final_text", "done")


class _ContextWithMessage(FakeRequestContext):
    def __init__(self, metadata: dict[str, str]) -> None:
        super().__init__("hello")
        self.message = Message(
            message_id="m", role=Role.ROLE_USER, parts=[Part(text="hello")], metadata=metadata
        )


def _executor(loop: _RecordingToolLoop) -> GenericAgentExecutor:
    async def settings() -> FakeAgentSettings:
        return FakeAgentSettings()

    async def llm() -> LLMSettings:
        return LLMSettings(llm_base_url="https://x/", llm_api_key="k", llm_model="m")

    return GenericAgentExecutor(
        agent_name="Testagent",
        logger=logging.getLogger("red.testagent"),
        tool_loop=loop,  # type: ignore[arg-type]
        tools=[],
        settings=settings,
        llm_settings=llm,
    )


class TestExecutorSetsTheGuild(unittest.IsolatedAsyncioTestCase):
    async def test_the_turn_runs_for_the_guild_the_request_names(self) -> None:
        loop = _RecordingToolLoop()
        await _executor(loop).execute(
            _ContextWithMessage(guild_metadata(GUILD_ID)),  # type: ignore[arg-type]
            FakeEventQueue(),  # type: ignore[arg-type]
        )
        self.assertEqual(loop.seen, [GUILD_ID])
        self.assertIsNone(activity_guild_id())

    async def test_a_request_without_a_guild_runs_for_none(self) -> None:
        loop = _RecordingToolLoop()
        with activity_guild(GUILD_ID):
            await _executor(loop).execute(
                _ContextWithMessage({}),  # type: ignore[arg-type]
                FakeEventQueue(),  # type: ignore[arg-type]
            )
        self.assertEqual(loop.seen, [None])
