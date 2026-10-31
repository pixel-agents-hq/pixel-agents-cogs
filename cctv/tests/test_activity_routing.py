"""Which pages a genuine agent's activity reaches: the guild whose
conversation produced it, and the editor -- never a guild of unknown origin."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any

from corridor.domain import (
    AgentHighlighted,
    AgentRef,
    AgentStatusChanged,
    AgentToolStarted,
)

from ..adapters.cog_base import CctvBase
from ..application import GuildOffices

ENABLED_GUILD = 10
DISABLED_GUILD = 20
AGENT = AgentRef(discord_user_id=None, guild_id=None, is_bot=True, agent_key="architect")


class _Office:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def start_tool_activity(self, *args: Any) -> None:
        self.calls.append("tool")

    async def set_status(self, *args: Any) -> None:
        self.calls.append("status")

    async def highlight_agent(self, *args: Any) -> None:
        self.calls.append("highlight")


class _Pipeline:
    def __init__(self, page: str) -> None:
        self.page = page
        self.office = _Office()


class _Harness(CctvBase):
    def __init__(self) -> None:
        # Only an enabled guild has an open office.
        self.mine = _Pipeline(f"office/{ENABLED_GUILD}")
        self.theirs = _Pipeline("office/30")
        self._offices = GuildOffices()
        self._offices.add(ENABLED_GUILD, self.mine)  # type: ignore[arg-type]
        self._offices.add(30, self.theirs)  # type: ignore[arg-type]
        self._editor = _Pipeline("editor")  # type: ignore[assignment]
        self.bot = SimpleNamespace(user=SimpleNamespace(id=99))  # type: ignore[assignment]

    def calls(self) -> tuple[list[str], list[str]]:
        assert self.theirs.office.calls == []
        return (self.mine.office.calls, self._editor.office.calls)  # type: ignore[union-attr]


class TestGenuineActivityRouting(unittest.IsolatedAsyncioTestCase):
    async def test_activity_for_an_enabled_guild_reaches_its_office_and_the_editor(self) -> None:
        cctv = _Harness()
        await cctv._on_agent_tool_started(
            AgentToolStarted(AGENT, "t1", "painting", context_guild_id=ENABLED_GUILD)
        )
        await cctv._on_agent_status_changed(
            AgentStatusChanged(AGENT, "active", context_guild_id=ENABLED_GUILD)
        )
        self.assertEqual(cctv.calls(), (["tool", "status"], ["tool", "status"]))

    async def test_activity_of_unknown_origin_reaches_only_the_editor(self) -> None:
        cctv = _Harness()
        await cctv._on_agent_tool_started(AgentToolStarted(AGENT, "t1", "painting"))
        await cctv._on_agent_highlighted(AgentHighlighted(AGENT))
        self.assertEqual(cctv.calls(), ([], ["tool", "highlight"]))

    async def test_activity_for_a_disabled_guild_reaches_only_the_editor(self) -> None:
        cctv = _Harness()
        await cctv._on_agent_tool_started(
            AgentToolStarted(AGENT, "t1", "painting", context_guild_id=DISABLED_GUILD)
        )
        self.assertEqual(cctv.calls(), ([], ["tool"]))
