"""FeedbackService: turns a report_error/suggest_improvement submission
into a post in this bot's configured feedback channel.

Depends only on a `FeedbackChannelRepository` Protocol (the configured
channel) and a plain `Poster` callable for the actual send -- never
corridor/discord.py/redbot directly, matching this package's other
application-layer modules. The adapter layer's real `Poster`
implementation is the only place that ever imports `corridor.domain` or
calls `corridor.send_channel_reply` (see docs/suggestionbox-design.md §3);
this service only decides *what* to say.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from typing import Protocol

from ..domain import ErrorReport, ImprovementSuggestion


class FeedbackChannelRepository(Protocol):
    async def feedback_channel(self) -> tuple[int, int] | None:
        """`(guild_id, channel_id)` of the configured feedback channel, or
        `None` if a bot owner hasn't set one yet."""
        ...


# (guild_id, channel_id, title, description, fields) -> True if the post
# succeeded. `fields` is a sequence of (name, value) pairs -- plain data,
# not corridor.domain.ReplyField, so this module stays corridor-agnostic.
Poster = Callable[[int, int, str, str, Sequence[tuple[str, str]]], Awaitable[bool]]

NOT_CONFIGURED_MESSAGE = (
    "No feedback channel has been configured yet. Ask the bot owner to run "
    "[p]suggestionbox channel."
)
CHANNEL_UNAVAILABLE_MESSAGE = "The configured feedback channel could not be reached."


class FeedbackService:
    def __init__(self, repository: FeedbackChannelRepository, *, post: Poster) -> None:
        self._repository = repository
        self._post = post

    async def report_error(self, report: ErrorReport) -> dict[str, object]:
        return await self._submit(
            title=f"🐞 Error report ({report.severity.value})",
            description=report.what_happened,
            fields=[
                ("Source", report.source),
                ("Expected", report.expected),
                ("Actual", report.actual),
            ],
        )

    async def suggest_improvement(self, suggestion: ImprovementSuggestion) -> dict[str, object]:
        return await self._submit(
            title="💡 Improvement suggestion",
            description=suggestion.observation,
            fields=[
                ("Source", suggestion.source),
                ("Area", suggestion.area),
                ("Suggestion", suggestion.suggestion),
            ],
        )

    async def _submit(
        self, *, title: str, description: str, fields: list[tuple[str, str]]
    ) -> dict[str, object]:
        configured = await self._repository.feedback_channel()
        if configured is None:
            return {
                "status": "error",
                "error": "not_configured",
                "message": NOT_CONFIGURED_MESSAGE,
            }
        guild_id, channel_id = configured
        posted = await self._post(guild_id, channel_id, title, description, fields)
        if not posted:
            return {
                "status": "error",
                "error": "channel_unavailable",
                "message": CHANNEL_UNAVAILABLE_MESSAGE,
            }
        return {"status": "ok"}


__all__ = [
    "CHANNEL_UNAVAILABLE_MESSAGE",
    "NOT_CONFIGURED_MESSAGE",
    "FeedbackChannelRepository",
    "FeedbackService",
    "Poster",
]
