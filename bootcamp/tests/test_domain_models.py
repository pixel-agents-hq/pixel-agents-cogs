"""Domain models need no mocking, no stubs, nothing framework-related --
that's the whole point of keeping this layer pure."""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from ..domain import DEFAULT_MAX_TOOL_CALLS, DEFAULT_PERMISSION_GROUP, CustomAgent


def test_custom_agent_holds_its_fields() -> None:
    agent = CustomAgent(
        agent_key="recruiter",
        system_prompt="You screen job applicants.",
        permission_group="keyholder",
        max_tool_calls=3,
        debug_logging=True,
        request_timeout_seconds=45.0,
        description="Consult for anything about screening job applicants.",
    )

    assert agent.agent_key == "recruiter"
    assert agent.system_prompt == "You screen job applicants."
    assert agent.permission_group == "keyholder"
    assert agent.max_tool_calls == 3
    assert agent.debug_logging is True
    assert agent.request_timeout_seconds == 45.0
    assert agent.description == "Consult for anything about screening job applicants."


def test_custom_agent_defaults_to_unrestricted_and_a_sane_tool_budget() -> None:
    agent = CustomAgent(agent_key="recruiter", system_prompt="You screen job applicants.")

    assert agent.permission_group == DEFAULT_PERMISSION_GROUP == "employee"
    assert agent.max_tool_calls == DEFAULT_MAX_TOOL_CALLS
    assert agent.debug_logging is False
    assert agent.request_timeout_seconds is None
    assert agent.description is None


def test_custom_agent_is_frozen() -> None:
    agent = CustomAgent(agent_key="recruiter", system_prompt="You screen job applicants.")

    with pytest.raises(FrozenInstanceError):
        agent.system_prompt = "something else"  # type: ignore[misc]
