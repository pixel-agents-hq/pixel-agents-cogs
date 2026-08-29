"""suggestionbox's actual MCP server: `report_error`/`suggest_improvement`,
built fresh (closing over that load's own `FeedbackService`) at `cog_load`
and whenever the listener restarts. See docs/suggestionbox-design.md §3.

Built against `mcp>=1.29,<2`'s `FastMCP` -- see `corridor/infrastructure/
mcp_client.py`'s own docstring for why this repo stays below `mcp` 2.0
(FastMCP renamed to MCPServer there, plus a `pydantic>=2.12` floor that
collides with every cog's `pydantic<2.12` pin).
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from ..application import FeedbackService
from ..domain import ErrorReport, ImprovementSuggestion, Severity

_SEVERITIES = tuple(severity.value for severity in Severity)


def build_mcp_server(service: FeedbackService, *, host: str, port: int) -> FastMCP:
    """`stateless_http=True`: each request is independent (no MCP session
    resumption needed for two one-shot report/suggest tools), matching
    corridor's `McpClientPool` opening a fresh connection per call rather
    than holding one open (see that module's own docstring)."""

    mcp = FastMCP("suggestionbox", host=host, port=port, stateless_http=True)

    @mcp.tool()
    async def report_error(
        source: str,
        what_happened: str,
        expected: str,
        actual: str,
        severity: str = Severity.MEDIUM.value,
    ) -> dict[str, object]:
        """Report an error or mistake you made while working -- for
        example, you misunderstood an available tool's description, or
        spent a long time reasoning before catching your own mistake
        early on. Posts to this bot's configured feedback channel.

        Args:
            source: Who is reporting -- e.g. "architect", or a free-text
                description of an external tool/session.
            what_happened: What went wrong, in your own words.
            expected: What you expected to happen.
            actual: What actually happened.
            severity: One of "low", "medium", "high".
        """

        if severity not in _SEVERITIES:
            return {
                "status": "error",
                "error": "invalid_severity",
                "message": f"severity must be one of: {', '.join(_SEVERITIES)}",
            }
        report = ErrorReport(
            source=source,
            what_happened=what_happened,
            expected=expected,
            actual=actual,
            severity=Severity(severity),
        )
        return await service.report_error(report)

    @mcp.tool()
    async def suggest_improvement(
        source: str, area: str, observation: str, suggestion: str
    ) -> dict[str, object]:
        """Suggest an improvement to this project -- for example, a tool
        description that was unclear, or documentation that was missing
        or wrong. Posts to this bot's configured feedback channel.

        Args:
            source: Who is suggesting this -- e.g. "architect", or a
                free-text description of an external tool/session.
            area: What part of the project this is about.
            observation: What you noticed.
            suggestion: What you'd change.
        """

        parsed = ImprovementSuggestion(
            source=source, area=area, observation=observation, suggestion=suggestion
        )
        return await service.suggest_improvement(parsed)

    return mcp


__all__ = ["build_mcp_server"]
