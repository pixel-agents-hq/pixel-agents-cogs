"""GateService: rule-based fast paths never touch the fake LLM classifier;
only the ambiguous "mentions 'pico' without addressing the bot" case does."""

from __future__ import annotations

import unittest
from collections.abc import Sequence

from corridor.infrastructure.llm_client import (
    ChatCompletionChoice,
    ChatCompletionResponse,
    ChatCompletionResponseMessage,
    ChatMessage,
    LLMRequestError,
)

from ..application.gate_service import GateService
from ..domain import ConversationContext, GateDecision, HistoryEntry, MessageSnapshot


def _snapshot(**overrides: object) -> MessageSnapshot:
    defaults: dict[str, object] = {
        "guild_id": 1,
        "channel_id": 2,
        "message_id": 3,
        "author_id": 4,
        "author_is_bot": False,
        "content": "hello",
        "mentions_bot": False,
        "is_reply_to_bot": False,
    }
    defaults.update(overrides)
    return MessageSnapshot(**defaults)  # type: ignore[arg-type]


class FakeGateLLM:
    def __init__(self, answer: str | None = "yes", *, error: Exception | None = None) -> None:
        self.answer = answer
        self.error = error
        self.calls: list[list[ChatMessage]] = []

    async def complete(
        self, *, base_url: str, api_key: str, model: str, messages: Sequence[ChatMessage]
    ) -> ChatCompletionResponse:
        self.calls.append(list(messages))
        if self.error is not None:
            raise self.error
        return ChatCompletionResponse(
            choices=[
                ChatCompletionChoice(
                    message=ChatCompletionResponseMessage(role="assistant", content=self.answer)
                )
            ]
        )


class TestRuleBasedFastPath(unittest.IsolatedAsyncioTestCase):
    async def test_reply_to_bot_responds_without_calling_the_llm(self) -> None:
        llm = FakeGateLLM()
        service = GateService(llm)
        context = ConversationContext(trigger=_snapshot(is_reply_to_bot=True, content="whatever"))

        decision = await service.decide(context, base_url="https://x", api_key="k", model="m")

        self.assertIs(decision, GateDecision.RESPOND)
        self.assertEqual(llm.calls, [])

    async def test_direct_mention_responds_without_calling_the_llm(self) -> None:
        llm = FakeGateLLM()
        service = GateService(llm)
        context = ConversationContext(trigger=_snapshot(mentions_bot=True, content="whatever"))

        decision = await service.decide(context, base_url="https://x", api_key="k", model="m")

        self.assertIs(decision, GateDecision.RESPOND)
        self.assertEqual(llm.calls, [])

    async def test_unrelated_message_is_ignored_without_calling_the_llm(self) -> None:
        llm = FakeGateLLM()
        service = GateService(llm)
        context = ConversationContext(trigger=_snapshot(content="what's for lunch"))

        decision = await service.decide(context, base_url="https://x", api_key="k", model="m")

        self.assertIs(decision, GateDecision.IGNORE)
        self.assertEqual(llm.calls, [])


class TestAmbiguousPicoMention(unittest.IsolatedAsyncioTestCase):
    async def test_word_boundary_match_calls_the_classifier(self) -> None:
        llm = FakeGateLLM(answer="yes")
        service = GateService(llm)
        context = ConversationContext(trigger=_snapshot(content="hey does pico work here"))

        decision = await service.decide(context, base_url="https://x", api_key="k", model="m")

        self.assertIs(decision, GateDecision.RESPOND)
        self.assertEqual(len(llm.calls), 1)

    async def test_substring_match_is_not_a_word_boundary_hit(self) -> None:
        llm = FakeGateLLM()
        service = GateService(llm)
        context = ConversationContext(trigger=_snapshot(content="I bought a picollo flute"))

        decision = await service.decide(context, base_url="https://x", api_key="k", model="m")

        self.assertIs(decision, GateDecision.IGNORE)
        self.assertEqual(llm.calls, [])

    async def test_classifier_no_answer_ignores(self) -> None:
        llm = FakeGateLLM(answer="no")
        service = GateService(llm)
        context = ConversationContext(trigger=_snapshot(content="pico is neat"))

        decision = await service.decide(context, base_url="https://x", api_key="k", model="m")

        self.assertIs(decision, GateDecision.IGNORE)

    async def test_history_is_included_as_context(self) -> None:
        llm = FakeGateLLM(answer="yes")
        service = GateService(llm)
        history = (
            HistoryEntry(author_name="alice", author_is_bot=False, content="anyone seen pico"),
        )
        context = ConversationContext(trigger=_snapshot(content="ask pico"), history=history)

        await service.decide(context, base_url="https://x", api_key="k", model="m")

        sent = llm.calls[0]
        self.assertTrue(any("alice" in (m.content or "") for m in sent))

    async def test_llm_failure_fails_closed(self) -> None:
        llm = FakeGateLLM(error=LLMRequestError("boom"))
        service = GateService(llm)
        context = ConversationContext(trigger=_snapshot(content="pico??"))

        decision = await service.decide(context, base_url="https://x", api_key="k", model="m")

        self.assertIs(decision, GateDecision.IGNORE)
