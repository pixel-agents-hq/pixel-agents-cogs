"""Domain models need no mocking, no stubs, nothing framework-related --
that's the whole point of keeping this layer pure."""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from ..domain import (
    ConversationContext,
    GateDecision,
    GlobalSettings,
    GuildSettings,
    HistoryEntry,
    MessageSnapshot,
)


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


def test_message_snapshot_holds_its_fields() -> None:
    snapshot = _snapshot(content="hi pico")

    assert snapshot.content == "hi pico"
    assert snapshot.guild_id == 1


def test_message_snapshot_is_frozen() -> None:
    snapshot = _snapshot()

    with pytest.raises(FrozenInstanceError):
        snapshot.content = "changed"  # type: ignore[misc]


def test_conversation_context_defaults_to_empty_history() -> None:
    context = ConversationContext(trigger=_snapshot())

    assert context.history == ()


def test_conversation_context_holds_history_in_order() -> None:
    history = (
        HistoryEntry(author_name="a", author_is_bot=False, content="1"),
        HistoryEntry(author_name="b", author_is_bot=True, content="2"),
    )

    context = ConversationContext(trigger=_snapshot(), history=history)

    assert context.history == history


def test_global_settings_ready_requires_both_key_and_model() -> None:
    neither = GlobalSettings(
        llm_base_url="x", llm_api_key=None, llm_model=None, max_tool_calls=5, system_prompt="p"
    )
    only_key = GlobalSettings(
        llm_base_url="x", llm_api_key="k", llm_model=None, max_tool_calls=5, system_prompt="p"
    )
    only_model = GlobalSettings(
        llm_base_url="x", llm_api_key=None, llm_model="m", max_tool_calls=5, system_prompt="p"
    )
    both = GlobalSettings(
        llm_base_url="x", llm_api_key="k", llm_model="m", max_tool_calls=5, system_prompt="p"
    )

    assert not neither.ready
    assert not only_key.ready
    assert not only_model.ready
    assert both.ready


def test_guild_settings_holds_its_fields() -> None:
    settings = GuildSettings(guild_id=1, enabled=True)

    assert settings.guild_id == 1
    assert settings.enabled is True


def test_gate_decision_values() -> None:
    assert GateDecision.RESPOND == "respond"
    assert GateDecision.IGNORE == "ignore"
