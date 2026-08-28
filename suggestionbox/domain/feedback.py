"""Pure business models. Zero framework imports -- this module never imports
discord.py, redbot, or mcp, so it is trivially unit-testable without any of
them installed.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Severity(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass(frozen=True, slots=True)
class ErrorReport:
    """One `report_error` submission. `source` is free text identifying the
    reporter -- "architect", "a Claude Code session on this repo", etc. --
    since neither an external MCP client nor a registered A2A agent's own
    call carries a Discord identity this cog could otherwise use. See
    docs/suggestionbox-design.md §3.
    """

    source: str
    what_happened: str
    expected: str
    actual: str
    severity: Severity


@dataclass(frozen=True, slots=True)
class ImprovementSuggestion:
    """One `suggest_improvement` submission -- same `source` rationale as
    `ErrorReport`."""

    source: str
    area: str
    observation: str
    suggestion: str


__all__ = ["ErrorReport", "ImprovementSuggestion", "Severity"]
