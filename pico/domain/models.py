"""Pure business models. Zero framework imports -- this module never imports
discord.py, redbot, or pydantic, so it is trivially unit-testable without
any of them installed."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class GateDecision(StrEnum):
    """What GateService.decide() concluded about one trigger message."""

    RESPOND = "respond"
    IGNORE = "ignore"


@dataclass(frozen=True, slots=True)
class MessageSnapshot:
    """The Discord message fields the evaluation gate and tool loop need,
    framework-neutral. `mentions_bot`/`is_reply_to_bot` are pre-computed by
    the adapter layer (resolving discord.py's Message.mentions/reference)
    so this module never needs to know how those are derived."""

    guild_id: int
    channel_id: int
    message_id: int
    author_id: int
    author_is_bot: bool
    content: str
    mentions_bot: bool
    is_reply_to_bot: bool


@dataclass(frozen=True, slots=True)
class HistoryEntry:
    """One prior message from the same channel, kept only as classifier/LLM
    context -- never re-sent or acted on directly."""

    author_name: str
    author_is_bot: bool
    content: str


@dataclass(frozen=True, slots=True)
class ConversationContext:
    """The trigger message plus whatever recent channel history the gate
    classifier and tool loop are given as context. `history` is oldest
    first, matching chat-completion message ordering."""

    trigger: MessageSnapshot
    history: tuple[HistoryEntry, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class GlobalSettings:
    """Owner (bot-wide) turn-budget/prompt settings. The LLM *connection*
    (endpoint/key/model) moved to corridor -- see
    docs/architect-design.md's LLM provider migration section and
    `corridor.domain.LLMSettings`; pico now reads it through
    `self._corridor.llm_settings()` instead of owning it here.

    There is no `architect_url` field here anymore -- pico builds one
    `consult_<agent_key>` tool per entry in
    `self._corridor.list_agents()` each turn instead of one hardcoded
    A2A target; see docs/agent-directory-design.md."""

    max_tool_calls: int
    system_prompt: str


@dataclass(frozen=True, slots=True)
class GuildSettings:
    """Per-guild settings. `enabled` defaults off -- a guild admin must
    opt in before Pico reacts to anything in that server."""

    guild_id: int
    enabled: bool
