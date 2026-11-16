"""Enumerates commands eligible for toolbox's tool-toggle panel.

Filters `hidden`/`enabled`/`can_run` the same way Red's own help formatter
does (`redbot/core/commands/help.py::RedHelpFormatter.help_filter_func`) --
so "what toolbox offers to wrap" matches "what `[p]help` would show that
same invoker", not a separate, harder-to-explain eligibility rule. Takes a
plain iterable of command-like objects rather than a live Cog/Bot, so it's
testable against small stand-ins the same way corridor's
llm_tool_registration.py and toolbox's own tool_wrapping.py are -- a real
`bot.walk_commands()` result satisfies the same shape.

Bot-owner-only commands (Red's `@commands.is_owner()`, which sets
`requires.privilege_level` to `BOT_OWNER`, on the command or any parent
group) are never candidates, even for the owner who opens the panel: a
pico turn reads other members' messages too, and those could steer the
LLM into calling `[p]eval` or `[p]shutdown` while the owner is talking.
`tool_wrapping.py` applies the same rule to names already selected.

An optional `search` narrows the result to commands whose qualified name
contains it (case-insensitive substring match) -- checked before the
hidden/enabled/can_run filters so a non-matching command never pays for an
awaited `can_run`.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from corridor.domain.llm_tools import llm_tool_spec


def is_owner_only(command: object) -> bool:
    """Whether Red restricts `command`, or a group above it, to the bot
    owner. Duck-typed on `requires.privilege_level.name` so it holds for
    stand-ins without Red installed."""

    current: object | None = command
    while current is not None:
        level = getattr(getattr(current, "requires", None), "privilege_level", None)
        if getattr(level, "name", None) == "BOT_OWNER":
            return True
        current = getattr(current, "parent", None)
    return False


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
    commands: Iterable[Any], ctx: Any, selected: frozenset[str], search: str | None = None
) -> list[CandidateCommand]:
    needle = search.lower() if search else None
    seen: set[str] = set()
    candidates: list[CandidateCommand] = []
    for command in commands:
        qualified_name = getattr(command, "qualified_name", None)
        if not isinstance(qualified_name, str) or not qualified_name or qualified_name in seen:
            continue
        if needle is not None and needle not in qualified_name.lower():
            continue
        if getattr(command, "hidden", False):
            continue
        if not getattr(command, "enabled", True):
            continue
        if is_owner_only(command):
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


__all__ = ["CandidateCommand", "is_owner_only", "list_candidate_commands"]
