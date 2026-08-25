"""Enumerates commands eligible for toolbox's tool-toggle panel.

Filters `hidden`/`enabled`/`can_run` the same way Red's own help formatter
does (`redbot/core/commands/help.py::RedHelpFormatter.help_filter_func`) --
so "what toolbox offers to wrap" matches "what `[p]help` would show that
same invoker", not a separate, harder-to-explain eligibility rule. Takes a
plain iterable of command-like objects rather than a live Cog/Bot, so it's
testable against small stand-ins the same way corridor's
llm_tool_registration.py and toolbox's own tool_wrapping.py are -- a real
`bot.walk_commands()` result satisfies the same shape.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from corridor.domain.llm_tools import llm_tool_spec


@dataclass(frozen=True, slots=True)
class CandidateCommand:
    """One command eligible for toolbox's panel, plus enough state to
    render its current row."""

    qualified_name: str
    tool_name: str
    short_doc: str
    already_decorated: bool
    selected: bool


async def list_candidate_commands(
    commands: Iterable[Any], ctx: Any, selected: frozenset[str]
) -> list[CandidateCommand]:
    seen: set[str] = set()
    candidates: list[CandidateCommand] = []
    for command in commands:
        qualified_name = getattr(command, "qualified_name", None)
        if not isinstance(qualified_name, str) or not qualified_name or qualified_name in seen:
            continue
        if getattr(command, "hidden", False):
            continue
        if not getattr(command, "enabled", True):
            continue
        can_run = getattr(command, "can_run", None)
        if callable(can_run):
            try:
                if not await can_run(ctx):
                    continue
            except Exception:
                continue
        seen.add(qualified_name)
        callback = getattr(command, "callback", None)
        already_decorated = callback is not None and llm_tool_spec(callback) is not None
        short_doc = getattr(command, "short_doc", "") or "(no description)"
        candidates.append(
            CandidateCommand(
                qualified_name=qualified_name,
                tool_name="_".join(qualified_name.split()),
                short_doc=short_doc,
                already_decorated=already_decorated,
                selected=qualified_name in selected,
            )
        )
    candidates.sort(key=lambda candidate: candidate.qualified_name)
    return candidates


__all__ = ["CandidateCommand", "list_candidate_commands"]
