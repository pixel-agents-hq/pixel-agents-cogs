"""GateService: decide *whether* Pico should react to a message at all.

Cheap rule-based fast path first (never touches the LLM), falling back to
one LLM classification call only for the genuinely ambiguous case: the
message mentions "pico" without directly addressing the bot. That
classification call does not count against the per-turn `max_tool_calls`
budget -- that budget bounds only `ToolLoopService`'s tool-calling loop.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from typing import Protocol

from corridor.infrastructure.llm_client import ChatCompletionResponse, ChatMessage, LLMRequestError

from ..domain import ConversationContext, GateDecision

log = logging.getLogger("red.pico")

_PICO_MENTION = re.compile(r"(?i)\bpico\b")

_CLASSIFIER_SYSTEM_PROMPT = (
    "You are a classifier deciding whether Pico, a Discord bot, should respond to "
    'the latest message below. The message mentions the word "pico" only in '
    "passing -- it does not directly address or reply to the bot. Reply with "
    'exactly one word: "yes" if the message is actually talking to or about Pico '
    'in a way that expects a reply, or "no" if it should stay silent.'
)


class GateLLM(Protocol):
    """The narrow slice of LiteLLMClient this service depends on -- no
    tools/tool_choice, unlike ToolLoopService's ToolLLM Protocol."""

    async def complete(
        self, *, base_url: str, api_key: str, model: str, messages: Sequence[ChatMessage]
    ) -> ChatCompletionResponse: ...


class GateService:
    def __init__(self, llm: GateLLM) -> None:
        self._llm = llm

    async def decide(
        self,
        context: ConversationContext,
        *,
        base_url: str,
        api_key: str,
        model: str,
    ) -> GateDecision:
        trigger = context.trigger
        if trigger.is_reply_to_bot or trigger.mentions_bot:
            return GateDecision.RESPOND
        if not _PICO_MENTION.search(trigger.content):
            return GateDecision.IGNORE
        return await self._classify(context, base_url=base_url, api_key=api_key, model=model)

    async def _classify(
        self, context: ConversationContext, *, base_url: str, api_key: str, model: str
    ) -> GateDecision:
        messages = [ChatMessage(role="system", content=_CLASSIFIER_SYSTEM_PROMPT)]
        messages.extend(
            ChatMessage(
                role="assistant" if entry.author_is_bot else "user",
                content=f"{entry.author_name}: {entry.content}",
            )
            for entry in context.history
        )
        messages.append(ChatMessage(role="user", content=context.trigger.content))

        try:
            response = await self._llm.complete(
                base_url=base_url, api_key=api_key, model=model, messages=messages
            )
        except LLMRequestError as exc:
            log.warning("pico: gate classification call failed, ignoring message: %s", exc)
            return GateDecision.IGNORE

        if not response.choices:
            return GateDecision.IGNORE
        answer = (response.choices[0].message.content or "").strip().lower()
        return GateDecision.RESPOND if answer.startswith("y") else GateDecision.IGNORE


__all__ = ["GateLLM", "GateService"]
